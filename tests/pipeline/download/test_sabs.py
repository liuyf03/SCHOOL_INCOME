"""Tests for the SABS downloader."""
import geopandas as gpd
import pytest
from shapely.geometry import Point

from pipeline.download import sabs


def _make_gdf(state_codes):
    return gpd.GeoDataFrame(
        {"STATEFP": list(state_codes), "name": [f"s{i}" for i in range(len(state_codes))]},
        geometry=[Point(0, i) for i in range(len(state_codes))],
        crs="EPSG:4326",
    )


def test_filter_to_wa_keeps_only_state_53():
    gdf = _make_gdf(["53", "06", "53", "41", "53"])
    out = sabs.filter_to_wa(gdf)
    assert len(out) == 3
    assert set(out["STATEFP"]) == {"53"}


def test_filter_to_wa_returns_empty_when_no_wa_rows():
    gdf = _make_gdf(["06", "41"])
    out = sabs.filter_to_wa(gdf)
    assert len(out) == 0


def test_filter_to_wa_raises_on_missing_column():
    gdf = gpd.GeoDataFrame(
        {"foo": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326"
    )
    with pytest.raises(ValueError, match="STATEFP"):
        sabs.filter_to_wa(gdf)


def test_fetch_skips_when_output_exists(tmp_path, monkeypatch):
    target = tmp_path / "sabs.gpkg"
    target.write_bytes(b"already here")
    monkeypatch.setitem(sabs.RAW_FILES, "sabs", target)

    results = sabs.fetch(force=False)

    assert results == [("SABS", "skipped", target)]
