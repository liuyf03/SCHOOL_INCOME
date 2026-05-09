"""Pipeline core: pure functions for the spatial join + areal interpolation.

Exposes building blocks that the orchestrator (Phase 6) wires together:

- ``prepare_layers``  — reproject to TARGET_CRS, repair invalid geometries.
- ``attach_acs``      — left-join the three ACS tables onto block groups
                        by GEOID.
- ``interpolate_to_zones`` — dasymetric areal interpolation from block
                        groups to attendance zones, using census blocks
                        as the population auxiliary layer.
- ``compute_summary_stats`` — turn interpolated bracket counts into the
                        per-school summary record (median, shares,
                        histogram).
- ``flag_low_confidence`` — flag schools with missing SABS polygons or
                        very small interpolated household counts.

Everything here is deliberately pure and operates on small, ordinary
DataFrames / GeoDataFrames so the math can be exercised with synthetic
geometries in unit tests.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import geopandas as gpd
import pandas as pd
import shapely

from pipeline.brackets import (
    BRACKETS,
    BRACKET_COLUMNS,
    SHARE_OVER_150K_CUTOFF,
    SHARE_UNDER_35K_CUTOFF,
    families_with_children_columns,
)
from pipeline.io import TARGET_CRS

LOW_HOUSEHOLD_THRESHOLD = 50

REASON_MISSING_SABS = "missing_sabs"
REASON_LOW_HOUSEHOLD = "low_household_count"


# ---------------------------------------------------------------------------
# Layer preparation
# ---------------------------------------------------------------------------


def prepare_layers(
    sabs: gpd.GeoDataFrame,
    bgs: gpd.GeoDataFrame,
    blocks: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Reproject all three layers to ``TARGET_CRS`` and repair geometries.

    Self-intersections, slivers, and other validity issues that would
    crash the overlay step are fixed via ``shapely.make_valid``. Refuses
    to silently assume a CRS for an input layer that lacks one.
    """
    out: list[gpd.GeoDataFrame] = []
    for layer in (sabs, bgs, blocks):
        if layer.crs is None:
            raise ValueError("layer has no CRS; refusing to assume one")
        layer = layer.to_crs(TARGET_CRS).copy()
        layer["geometry"] = shapely.make_valid(layer.geometry.values)
        out.append(layer)
    return tuple(out)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# ACS join
# ---------------------------------------------------------------------------


def attach_acs(
    bgs: gpd.GeoDataFrame,
    b19131: pd.DataFrame,
    b11005: pd.DataFrame,
    b19013: pd.DataFrame,
    geoid_col: str = "GEOID",
) -> gpd.GeoDataFrame:
    """Left-join the three ACS tables onto block groups by GEOID."""
    if geoid_col not in bgs.columns:
        raise ValueError(f"block-group layer missing {geoid_col!r}")

    needed_19131 = [geoid_col, *families_with_children_columns()]
    present_19131 = [c for c in needed_19131 if c in b19131.columns]

    out = bgs.merge(b19131[present_19131], on=geoid_col, how="left")

    if "B11005_001E" in b11005.columns:
        out = out.merge(b11005[[geoid_col, "B11005_001E"]], on=geoid_col, how="left")
    if "B19013_001E" in b19013.columns:
        out = out.merge(b19013[[geoid_col, "B19013_001E"]], on=geoid_col, how="left")

    return out


# ---------------------------------------------------------------------------
# Dasymetric interpolation
# ---------------------------------------------------------------------------


def interpolate_to_zones(
    zones: gpd.GeoDataFrame,
    bgs_with_acs: gpd.GeoDataFrame,
    blocks: gpd.GeoDataFrame,
    extensive_cols: Sequence[str],
    *,
    weight_col: str = "POP20",
    bg_geoid_col: str = "GEOID",
) -> gpd.GeoDataFrame:
    """Dasymetric areal interpolation of extensive variables.

    For each (block-group, zone) pair we accumulate the share of the
    block-group's population (per ``weight_col`` on the auxiliary
    ``blocks`` layer) that falls inside the zone, then distribute the
    block-group's extensive values to zones in proportion. This is the
    standard population-weighted areal interpolation: equal-area but
    differently-populated halves of a block group don't split bracket
    counts equally.

    Parameters
    ----------
    zones : the target polygons (school attendance areas).
    bgs_with_acs : block groups already joined to ACS estimates.
    blocks : auxiliary census blocks carrying the population weight.
    extensive_cols : ACS columns to interpolate (counts, summed not averaged).
    weight_col : name of the population column on ``blocks`` (default POP20).
    bg_geoid_col : block-group GEOID column shared by ``bgs_with_acs`` and
        (after spatial join, if needed) ``blocks``.

    Returns the zones layer with one new column per ``extensive_cols``.
    """
    zones = zones.reset_index(drop=True).copy()
    zones["__zone_id"] = range(len(zones))

    bgs = bgs_with_acs.reset_index(drop=True).copy()
    blocks = blocks.reset_index(drop=True).copy()

    # Tag each block with its parent BG via spatial join when not already tagged.
    if bg_geoid_col not in blocks.columns:
        blocks = gpd.sjoin(
            blocks,
            bgs[[bg_geoid_col, "geometry"]],
            how="left",
            predicate="within",
        ).drop(columns="index_right", errors="ignore")

    # Distribute each block's weight to overlapping zones by area share.
    blocks["__block_area"] = blocks.geometry.area
    pieces = gpd.overlay(
        blocks[[bg_geoid_col, weight_col, "__block_area", "geometry"]],
        zones[["__zone_id", "geometry"]],
        how="intersection",
        keep_geom_type=False,
    )
    pieces["__piece_area"] = pieces.geometry.area
    pieces["__effective_weight"] = (
        pieces[weight_col].fillna(0)
        * pieces["__piece_area"]
        / pieces["__block_area"].where(pieces["__block_area"] > 0, 1.0)
    )

    # share(BG → zone) = sum of effective weights captured / total BG weight
    weight_per_bg_zone = (
        pieces.groupby([bg_geoid_col, "__zone_id"])["__effective_weight"]
        .sum()
        .reset_index()
    )
    weight_per_bg = (
        blocks.groupby(bg_geoid_col)[weight_col]
        .sum()
        .rename("__bg_total_weight")
        .reset_index()
    )
    shares = weight_per_bg_zone.merge(weight_per_bg, on=bg_geoid_col, how="left")
    shares["__share"] = shares["__effective_weight"] / shares[
        "__bg_total_weight"
    ].where(shares["__bg_total_weight"] > 0, 1.0)
    shares.loc[shares["__bg_total_weight"] == 0, "__share"] = 0.0

    # Apply share to extensive variables and aggregate into zones.
    bg_subset = bgs[[bg_geoid_col, *extensive_cols]].copy()
    for col in extensive_cols:
        bg_subset[col] = bg_subset[col].fillna(0)
    distributed = shares.merge(bg_subset, on=bg_geoid_col, how="left")
    for col in extensive_cols:
        distributed[col] = distributed[col].fillna(0) * distributed["__share"]

    aggregated = distributed.groupby("__zone_id")[list(extensive_cols)].sum().reset_index()

    result = zones.merge(aggregated, on="__zone_id", how="left")
    for col in extensive_cols:
        result[col] = result[col].fillna(0)
    return result.drop(columns="__zone_id")


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------


def _bracket_total(record: Mapping[str, Any], cols: tuple[str, str, str]) -> float:
    total = 0.0
    for col in cols:
        val = record.get(col)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f != f:  # NaN
            continue
        total += f
    return total


def compute_summary_stats(record: Mapping[str, Any]) -> dict[str, Any]:
    """Reduce interpolated bracket counts into the per-school payload.

    Returns a dict with the schema fields documented in docs/schema.md:
    ``median_family_income``, ``share_under_35k``, ``share_over_150k``,
    ``total_families_with_children``, ``bracket_histogram``.

    Median is computed by linear interpolation across the cumulative
    distribution within the bracket containing the median household.
    Returns ``None`` for the median / share fields when the total is zero
    (e.g. a school with a missing SABS polygon).
    """
    bracket_counts = [_bracket_total(record, cols) for cols in BRACKET_COLUMNS]
    total = sum(bracket_counts)

    histogram = [
        {
            "label": bracket.label,
            "lower": bracket.lower,
            "upper": bracket.upper,
            "count": float(count),
        }
        for bracket, count in zip(BRACKETS, bracket_counts)
    ]

    if total <= 0:
        return {
            "median_family_income": None,
            "share_under_35k": None,
            "share_over_150k": None,
            "total_families_with_children": 0,
            "bracket_histogram": histogram,
        }

    half = total / 2.0
    cumulative = 0.0
    median: float | None = None
    for bracket, count in zip(BRACKETS, bracket_counts):
        next_cum = cumulative + count
        if next_cum >= half:
            if bracket.upper is None or count <= 0:
                median = float(bracket.lower)
            else:
                fraction = (half - cumulative) / count
                median = bracket.lower + fraction * (bracket.upper - bracket.lower)
            break
        cumulative = next_cum

    under_35k = sum(
        c for b, c in zip(BRACKETS, bracket_counts)
        if b.upper is not None and b.upper <= SHARE_UNDER_35K_CUTOFF
    )
    over_150k = sum(
        c for b, c in zip(BRACKETS, bracket_counts)
        if b.lower >= SHARE_OVER_150K_CUTOFF
    )

    return {
        "median_family_income": int(round(median)) if median is not None else None,
        "share_under_35k": under_35k / total,
        "share_over_150k": over_150k / total,
        "total_families_with_children": int(round(total)),
        "bracket_histogram": histogram,
    }


# ---------------------------------------------------------------------------
# Low-confidence flagging
# ---------------------------------------------------------------------------


def flag_low_confidence(record: Mapping[str, Any]) -> tuple[bool, list[str]]:
    """Return ``(is_low_confidence, reasons)`` for a per-school record.

    A record is low-confidence when:
    - Its SABS polygon is missing (``geometry`` is ``None`` or empty), or
    - Its interpolated total of families with children is below
      ``LOW_HOUSEHOLD_THRESHOLD`` (sampling noise dominates).

    Both conditions are independent and may both apply.
    """
    reasons: list[str] = []

    geometry = record.get("geometry")
    if geometry is None:
        reasons.append(REASON_MISSING_SABS)
    else:
        is_empty = getattr(geometry, "is_empty", False)
        if is_empty:
            reasons.append(REASON_MISSING_SABS)

    total = record.get("total_families_with_children")
    if total is None or total < LOW_HOUSEHOLD_THRESHOLD:
        reasons.append(REASON_LOW_HOUSEHOLD)

    return (bool(reasons), reasons)
