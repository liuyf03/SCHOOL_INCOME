"""Pipeline core + end-to-end orchestrator.

Pure functions exposed for unit testing on synthetic geometries:

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
- ``join_ccd``        — left-join CCD onto zones by NCESSCH; preserves
                        CCD-only schools with null geometry.
- ``serialize_schools`` — produce ``schools_wa.json`` (object keyed by
                        nces_id) and ``search_index.json`` (slim array)
                        per docs/schema.md.

``main()`` wires them together end-to-end: load raw inputs from
``RAW_FILES``, interpolate, assemble per-school records, write the two
JSON outputs to ``PROCESSED_FILES``.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Iterable, Mapping, Sequence

import geopandas as gpd
import pandas as pd
import shapely

from pipeline import io
from pipeline.brackets import (
    BRACKETS,
    BRACKET_COLUMNS,
    SHARE_OVER_150K_CUTOFF,
    SHARE_UNDER_35K_CUTOFF,
    families_with_children_columns,
)
from pipeline.io import TARGET_CRS

log = logging.getLogger(__name__)

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
    is_nan = isinstance(geometry, float) and geometry != geometry
    if geometry is None or is_nan or getattr(geometry, "is_empty", False):
        reasons.append(REASON_MISSING_SABS)

    total = record.get("total_families_with_children")
    if total is None or total < LOW_HOUSEHOLD_THRESHOLD:
        reasons.append(REASON_LOW_HOUSEHOLD)

    return (bool(reasons), reasons)


# ---------------------------------------------------------------------------
# CCD join
# ---------------------------------------------------------------------------


def join_ccd(
    zones: pd.DataFrame,
    ccd: pd.DataFrame,
    *,
    sabs_id_col: str = "ncessch",
) -> pd.DataFrame:
    """Left-join CCD onto interpolated zones by NCESSCH.

    Every CCD school is kept. Schools that have a CCD entry but no SABS
    polygon end up with null geometry and NaN bracket counts; downstream
    ``flag_low_confidence`` picks those up via the ``missing_sabs``
    reason. Orphan SABS polygons (zone with no matching CCD school) are
    dropped — without a school name we can't display them.
    """
    if "NCESSCH" not in ccd.columns:
        raise ValueError("CCD missing NCESSCH column")

    zones = zones.copy()
    if sabs_id_col in zones.columns and sabs_id_col != "NCESSCH":
        zones = zones.rename(columns={sabs_id_col: "NCESSCH"})

    ccd = ccd.copy()
    ccd["NCESSCH"] = ccd["NCESSCH"].astype(str).str.strip()
    if "NCESSCH" in zones.columns:
        zones["NCESSCH"] = zones["NCESSCH"].astype(str).str.strip()
        return ccd.merge(zones, on="NCESSCH", how="left", suffixes=("", "_zone"))

    # No zone identifier column at all — return CCD with empty zone fields.
    return ccd


# ---------------------------------------------------------------------------
# Record assembly + serialization
# ---------------------------------------------------------------------------


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value != value:
        return ""
    return str(value).strip()


def _format_grades(record: Mapping[str, Any]) -> str:
    """Build the canonical 'GSLO-GSHI' grade string ('K-5', '9-12', etc.)."""
    gslo = _safe_str(record.get("GSLO")).upper()
    gshi = _safe_str(record.get("GSHI")).upper()
    if not gslo and not gshi:
        return ""
    if gslo == gshi:
        return gslo
    if not gslo:
        return gshi
    if not gshi:
        return gslo
    return f"{gslo}-{gshi}"


def _format_address(record: Mapping[str, Any]) -> str:
    """'Street, City, State Zip' — drops missing parts cleanly."""
    street = _safe_str(record.get("LSTREET1"))
    city = _safe_str(record.get("LCITY"))
    state = _safe_str(record.get("LSTATE"))
    zip_ = _safe_str(record.get("LZIP"))

    locality = " ".join(p for p in (state, zip_) if p)
    locality = ", ".join(p for p in (city, locality) if p)
    return ", ".join(p for p in (street, locality) if p)


def _build_school_record(merged_row: Mapping[str, Any]) -> dict:
    """Compose one ``schools_wa.json`` entry from a CCD-joined zone row."""
    nces_id = _safe_str(merged_row.get("NCESSCH"))
    stats = compute_summary_stats(merged_row)
    flag, reasons = flag_low_confidence({**merged_row, **stats})

    return {
        "nces_id": nces_id,
        "name": _safe_str(merged_row.get("SCH_NAME")),
        "district": _safe_str(merged_row.get("LEA_NAME")),
        "city": _safe_str(merged_row.get("LCITY")),
        "grades": _format_grades(merged_row),
        "address": _format_address(merged_row),
        "median_family_income": stats["median_family_income"],
        "share_under_35k": stats["share_under_35k"],
        "share_over_150k": stats["share_over_150k"],
        "total_families_with_children": stats["total_families_with_children"],
        "bracket_histogram": stats["bracket_histogram"],
        "low_confidence": flag,
        "low_confidence_reasons": reasons,
    }


def serialize_schools(
    merged: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict], list[dict]]:
    """Format CCD-joined records into the two JSON payloads from
    docs/schema.md.

    Returns ``(schools_wa, search_index)``:
    - ``schools_wa`` is keyed by NCES ID; each value matches the full
      schema.
    - ``search_index`` is a slim sorted list of
      ``{nces_id, name, district, city}``.
    """
    schools: dict[str, dict] = {}
    for row in merged:
        record = _build_school_record(row)
        nces_id = record["nces_id"]
        if not nces_id:
            continue
        schools[nces_id] = record

    search_index = sorted(
        (
            {
                "nces_id": rec["nces_id"],
                "name": rec["name"],
                "district": rec["district"],
                "city": rec["city"],
            }
            for rec in schools.values()
        ),
        key=lambda r: r["nces_id"],
    )

    return schools, search_index


# ---------------------------------------------------------------------------
# End-to-end orchestrator
# ---------------------------------------------------------------------------


def _iter_rows(df: pd.DataFrame) -> Iterable[dict]:
    """Yield row dicts. Pulled out so main() can iterate without pandas
    bringing geometry through itertuples / iterrows."""
    for _, row in df.iterrows():
        yield row.to_dict()


def main() -> int:
    """Build ``schools_wa.json`` and ``search_index.json`` from the seven
    raw inputs in ``RAW_FILES``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    log.info("loading raw inputs")
    sabs = io.read_geo(io.RAW_FILES["sabs"])
    bgs = io.read_geo(io.RAW_FILES["block_groups"])
    blocks = io.read_geo(io.RAW_FILES["blocks"])
    b19131 = pd.read_parquet(io.RAW_FILES["acs_b19131"])
    b11005 = pd.read_parquet(io.RAW_FILES["acs_b11005"])
    b19013 = pd.read_parquet(io.RAW_FILES["acs_b19013"])
    ccd = pd.read_parquet(io.RAW_FILES["ccd"])
    log.info(
        "loaded: %d SABS zones, %d block groups, %d blocks, %d CCD schools",
        len(sabs), len(bgs), len(blocks), len(ccd),
    )

    log.info("preparing layers")
    sabs, bgs, blocks = prepare_layers(sabs, bgs, blocks)

    log.info("attaching ACS to block groups")
    bgs_with_acs = attach_acs(bgs, b19131, b11005, b19013)

    log.info("interpolating to %d zones (slowest step)", len(sabs))
    ev_cols = list(families_with_children_columns())
    zones_with_brackets = interpolate_to_zones(sabs, bgs_with_acs, blocks, ev_cols)

    log.info("joining CCD metadata onto %d zones", len(zones_with_brackets))
    merged = join_ccd(zones_with_brackets, ccd)
    log.info("merged: %d schools (CCD with optional zone)", len(merged))

    log.info("serializing")
    schools, search_index = serialize_schools(_iter_rows(merged))
    log.info("serialized %d schools", len(schools))

    # The full schools payload is multi-MB; compact format keeps it under
    # the 5 MB budget. The slim search index stays pretty for diff-readability.
    io.write_json(schools, io.PROCESSED_FILES["schools"], compact=True)
    io.write_json(search_index, io.PROCESSED_FILES["search_index"])
    log.info("wrote %s", io.PROCESSED_FILES["schools"])
    log.info("wrote %s", io.PROCESSED_FILES["search_index"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
