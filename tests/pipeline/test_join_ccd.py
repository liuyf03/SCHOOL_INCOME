"""Tests for join_ccd — left-merge CCD onto interpolated zones."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from pipeline.build_dataset import join_ccd
from pipeline.io import TARGET_CRS


def _square(x):
    return Polygon([(x, 0), (x + 1, 0), (x + 1, 1), (x, 1)])


def test_inner_join_keeps_matched_and_carries_through_unmatched():
    """3 SABS zones × 4 CCD rows. 3 schools have both polygon + CCD; 1
    has CCD but no SABS — must come through with null geometry."""
    zones = gpd.GeoDataFrame(
        {
            "ncessch": ["A", "B", "C"],
            "B19131_004E": [10.0, 20.0, 30.0],
        },
        geometry=[_square(0), _square(1), _square(2)],
        crs=TARGET_CRS,
    )
    ccd = pd.DataFrame(
        {
            "NCESSCH": ["A", "B", "C", "D"],
            "SCH_NAME": ["Alpha", "Beta", "Gamma", "Delta"],
        }
    )

    out = join_ccd(zones, ccd)

    assert len(out) == 4
    assert set(out["NCESSCH"]) == {"A", "B", "C", "D"}

    # Matched schools have their bracket counts
    matched = out.set_index("NCESSCH")
    assert matched.loc["A", "B19131_004E"] == 10.0
    assert matched.loc["B", "B19131_004E"] == 20.0

    # CCD-only school (D) has NaN bracket count and None geometry
    assert pd.isna(matched.loc["D", "B19131_004E"])
    assert matched.loc["D", "geometry"] is None or pd.isna(matched.loc["D", "geometry"])


def test_join_drops_orphan_sabs_polygons():
    """Zones whose NCESSCH is not in CCD must NOT appear in the output."""
    zones = gpd.GeoDataFrame(
        {"ncessch": ["A", "ORPHAN"]},
        geometry=[_square(0), _square(1)],
        crs=TARGET_CRS,
    )
    ccd = pd.DataFrame({"NCESSCH": ["A", "B"], "SCH_NAME": ["Alpha", "Beta"]})

    out = join_ccd(zones, ccd)

    assert set(out["NCESSCH"]) == {"A", "B"}
    assert "ORPHAN" not in out["NCESSCH"].values


def test_join_normalizes_id_whitespace_and_type():
    zones = gpd.GeoDataFrame(
        {"ncessch": ["  A  "], "B19131_004E": [42.0]},
        geometry=[_square(0)],
        crs=TARGET_CRS,
    )
    ccd = pd.DataFrame({"NCESSCH": ["A "], "SCH_NAME": ["Alpha"]})

    out = join_ccd(zones, ccd)

    assert len(out) == 1
    assert out["NCESSCH"].iloc[0] == "A"
    assert out["B19131_004E"].iloc[0] == 42.0


def test_join_raises_when_ccd_missing_nces_column():
    zones = pd.DataFrame({"ncessch": ["A"]})
    ccd = pd.DataFrame({"foo": ["bar"]})
    with pytest.raises(ValueError, match="NCESSCH"):
        join_ccd(zones, ccd)


def test_join_uses_explicit_sabs_id_column_name():
    """If the SABS layer uses a non-default column name, the parameter
    can override (e.g. some preprocessing renamed it)."""
    zones = gpd.GeoDataFrame(
        {"my_id": ["A"], "B19131_004E": [11.0]},
        geometry=[_square(0)],
        crs=TARGET_CRS,
    )
    ccd = pd.DataFrame({"NCESSCH": ["A"], "SCH_NAME": ["Alpha"]})

    out = join_ccd(zones, ccd, sabs_id_col="my_id")

    assert len(out) == 1
    assert out["B19131_004E"].iloc[0] == 11.0
