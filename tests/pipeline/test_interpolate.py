"""Tests for prepare_layers, attach_acs, interpolate_to_zones — all on
synthetic geometries so the math is checkable to a known answer."""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from pipeline.build_dataset import attach_acs, interpolate_to_zones, prepare_layers
from pipeline.io import TARGET_CRS


def _square(x0: float, y0: float, side: float) -> Polygon:
    return Polygon(
        [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    )


# ---------------------------------------------------------------------------
# prepare_layers
# ---------------------------------------------------------------------------


def test_prepare_layers_reprojects_to_target_crs():
    src = "EPSG:4326"
    sabs = gpd.GeoDataFrame({"id": [1]}, geometry=[_square(-122, 47, 0.1)], crs=src)
    bgs = gpd.GeoDataFrame({"id": [1]}, geometry=[_square(-122, 47, 0.1)], crs=src)
    blocks = gpd.GeoDataFrame({"id": [1]}, geometry=[_square(-122, 47, 0.05)], crs=src)

    out_sabs, out_bgs, out_blocks = prepare_layers(sabs, bgs, blocks)

    for layer in (out_sabs, out_bgs, out_blocks):
        assert layer.crs is not None
        assert layer.crs.to_epsg() == int(TARGET_CRS.split(":")[1])


def test_prepare_layers_raises_when_layer_has_no_crs():
    no_crs = gpd.GeoDataFrame({"id": [1]}, geometry=[_square(0, 0, 1)], crs=None)
    with_crs = gpd.GeoDataFrame({"id": [1]}, geometry=[_square(0, 0, 1)], crs=TARGET_CRS)
    with pytest.raises(ValueError, match="no CRS"):
        prepare_layers(no_crs, with_crs, with_crs)


def test_prepare_layers_repairs_invalid_geometry():
    """Bowtie polygon (self-intersecting) must come out valid so tobler
    overlay doesn't crash."""
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    layer = gpd.GeoDataFrame({"id": [1]}, geometry=[bowtie], crs=TARGET_CRS)
    other = gpd.GeoDataFrame({"id": [1]}, geometry=[_square(0, 0, 1)], crs=TARGET_CRS)

    out, _, _ = prepare_layers(layer, other, other)

    assert out.geometry.iloc[0].is_valid


# ---------------------------------------------------------------------------
# attach_acs
# ---------------------------------------------------------------------------


def test_attach_acs_left_joins_on_geoid():
    bgs = gpd.GeoDataFrame(
        {"GEOID": ["A", "B", "C"]},
        geometry=[_square(0, 0, 1), _square(1, 0, 1), _square(2, 0, 1)],
        crs=TARGET_CRS,
    )
    b19131 = pd.DataFrame({"GEOID": ["A", "B"], "B19131_004E": [10, 20]})
    b11005 = pd.DataFrame({"GEOID": ["A"], "B11005_001E": [100]})
    b19013 = pd.DataFrame({"GEOID": ["A", "B", "C"], "B19013_001E": [50000, 60000, 70000]})

    out = attach_acs(bgs, b19131, b11005, b19013)

    assert len(out) == 3
    assert out.loc[out["GEOID"] == "A", "B19131_004E"].iloc[0] == 10
    assert pd.isna(out.loc[out["GEOID"] == "C", "B19131_004E"].iloc[0])
    assert out.loc[out["GEOID"] == "C", "B19013_001E"].iloc[0] == 70000


def test_attach_acs_raises_when_geoid_missing():
    bgs = gpd.GeoDataFrame(
        {"foo": ["A"]}, geometry=[_square(0, 0, 1)], crs=TARGET_CRS
    )
    empty = pd.DataFrame({"GEOID": [], "B19131_004E": []})
    with pytest.raises(ValueError, match="GEOID"):
        attach_acs(bgs, empty, empty, empty)


def test_attach_acs_tolerates_missing_b19131_columns():
    """Real ACS responses may omit some bracket columns when all values
    are suppressed for privacy. attach_acs should not crash."""
    bgs = gpd.GeoDataFrame(
        {"GEOID": ["A"]}, geometry=[_square(0, 0, 1)], crs=TARGET_CRS
    )
    b19131 = pd.DataFrame({"GEOID": ["A"], "B19131_004E": [5]})  # only one bracket col
    empty = pd.DataFrame({"GEOID": ["A"]})

    out = attach_acs(bgs, b19131, empty, empty)

    assert "B19131_004E" in out.columns
    assert out["B19131_004E"].iloc[0] == 5


# ---------------------------------------------------------------------------
# interpolate_to_zones
# ---------------------------------------------------------------------------


def _make_bg_blocks_zone(
    bg_geom,
    block_specs: list[tuple[Polygon, float]],  # (geometry, POP20)
    zone_geom,
    bracket_cols: dict[str, float] | None = None,
):
    bg_data = {"GEOID": ["BG1"]}
    if bracket_cols:
        for col, value in bracket_cols.items():
            bg_data[col] = [value]
    bgs = gpd.GeoDataFrame(bg_data, geometry=[bg_geom], crs=TARGET_CRS)
    blocks = gpd.GeoDataFrame(
        {"POP20": [w for _, w in block_specs]},
        geometry=[g for g, _ in block_specs],
        crs=TARGET_CRS,
    )
    zones = gpd.GeoDataFrame(
        {"nces_id": ["S1"]}, geometry=[zone_geom], crs=TARGET_CRS
    )
    return bgs, blocks, zones


def test_full_zone_contains_one_bg_receives_100_percent():
    """Zone fully containing a single BG must receive 100% of its counts."""
    bg = _square(0, 0, 10)
    blocks = [
        (_square(0, 0, 5), 50),
        (_square(5, 0, 5), 50),
        (_square(0, 5, 5), 50),
        (_square(5, 5, 5), 50),
    ]
    zone = _square(-1, -1, 12)  # strictly contains the BG
    bgs, blocks_gdf, zones = _make_bg_blocks_zone(
        bg, blocks, zone, bracket_cols={"B19131_004E": 100}
    )

    out = interpolate_to_zones(zones, bgs, blocks_gdf, ["B19131_004E"])

    assert pytest.approx(out["B19131_004E"].iloc[0], rel=0.01) == 100


def test_half_zone_gets_half_when_blocks_uniformly_populated():
    """Zone covering exactly half a uniformly-populated BG → ~50%."""
    bg = _square(0, 0, 10)
    blocks = [
        (_square(0, 0, 5), 50),  # left-bottom
        (_square(5, 0, 5), 50),  # right-bottom
        (_square(0, 5, 5), 50),  # left-top
        (_square(5, 5, 5), 50),  # right-top
    ]
    zone = _square(0, 0, 5)  # exactly the bottom-left quadrant (1 of 4 blocks)
    bgs, blocks_gdf, zones = _make_bg_blocks_zone(
        bg, blocks, zone, bracket_cols={"B19131_004E": 100}
    )

    out = interpolate_to_zones(zones, bgs, blocks_gdf, ["B19131_004E"])

    # 1 of 4 equally-populated blocks falls in zone → 25%
    assert pytest.approx(out["B19131_004E"].iloc[0], rel=0.01) == 25


def test_population_weighting_skews_toward_dense_block():
    """The core dasymetric correctness test: two equal-area BG halves with
    population concentrated in one block. A zone covering only that block
    must get ~100% of the BG's count, not 50% as pure area weighting would."""
    bg = _square(0, 0, 10)
    populated_block = _square(0, 0, 10)  # bottom half (full width, half height)
    populated_block = Polygon([(0, 0), (10, 0), (10, 5), (0, 5)])
    empty_block = Polygon([(0, 5), (10, 5), (10, 10), (0, 10)])  # top half
    blocks = [(populated_block, 100), (empty_block, 0)]
    zone = Polygon([(0, 0), (10, 0), (10, 5), (0, 5)])  # covers only populated block

    bgs, blocks_gdf, zones = _make_bg_blocks_zone(
        bg, blocks, zone, bracket_cols={"B19131_004E": 200}
    )

    out = interpolate_to_zones(zones, bgs, blocks_gdf, ["B19131_004E"])

    # Pure area weighting would give 100 (50% of BG by area).
    # Dasymetric must give ~200 because all the population lives in the
    # block this zone captures.
    assert pytest.approx(out["B19131_004E"].iloc[0], rel=0.01) == 200


def test_zone_fully_outside_any_bg_receives_zero():
    """A zone that doesn't intersect any block must end up with zero counts."""
    bg = _square(0, 0, 10)
    blocks = [(_square(0, 0, 5), 50), (_square(5, 0, 5), 50)]
    zone = _square(100, 100, 10)
    bgs, blocks_gdf, zones = _make_bg_blocks_zone(
        bg, blocks, zone, bracket_cols={"B19131_004E": 1000}
    )

    out = interpolate_to_zones(zones, bgs, blocks_gdf, ["B19131_004E"])

    assert out["B19131_004E"].iloc[0] == 0
