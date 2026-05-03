"""Tests for pipeline.io paths, constants, and helpers."""
from pathlib import Path

import geopandas as gpd
import pyproj
import pytest
from shapely.geometry import Point

from pipeline import io


def test_paths_resolve_under_repo_root():
    assert io.REPO_ROOT.is_dir()
    assert io.DATA_DIR == io.REPO_ROOT / "data"
    assert io.RAW_DIR == io.DATA_DIR / "raw"
    assert io.PROCESSED_DIR == io.DATA_DIR / "processed"
    assert io.RAW_DIR.is_dir()
    assert io.PROCESSED_DIR.is_dir()


def test_target_crs_is_projected():
    crs = pyproj.CRS(io.TARGET_CRS)
    assert crs.is_projected, (
        f"{io.TARGET_CRS} must be projected — tobler areal interpolation is sensitive to area distortion"
    )


def test_acs_vintage_is_recent_int():
    assert isinstance(io.ACS_VINTAGE, int)
    assert io.ACS_VINTAGE >= 2020


def test_raw_files_keys_and_locations():
    expected = {
        "sabs",
        "block_groups",
        "blocks",
        "ccd",
        "acs_b19131",
        "acs_b11005",
        "acs_b19013",
    }
    assert set(io.RAW_FILES.keys()) == expected
    for path in io.RAW_FILES.values():
        assert isinstance(path, Path)
        assert path.parent == io.RAW_DIR


def test_processed_files_under_processed_dir():
    for path in io.PROCESSED_FILES.values():
        assert path.parent == io.PROCESSED_DIR


def test_write_then_read_json_roundtrip(tmp_path):
    obj = {"b": 2, "a": [1, 2, 3], "c": {"nested": True}}
    p = tmp_path / "subdir" / "out.json"
    io.write_json(obj, p)
    assert p.exists(), "write_json should create parent dirs"
    assert io.read_json(p) == obj


def test_write_json_sorts_keys(tmp_path):
    p = tmp_path / "out.json"
    io.write_json({"b": 1, "a": 2}, p)
    text = p.read_text(encoding="utf-8")
    assert text.index('"a"') < text.index('"b"')


def test_write_json_preserves_unicode(tmp_path):
    p = tmp_path / "out.json"
    io.write_json({"name": "Mukilteo"}, p)
    assert "Mukilteo" in p.read_text(encoding="utf-8")


def test_read_geo_reprojects(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"id": [1, 2]},
        geometry=[Point(-122.33, 47.61), Point(-122.20, 47.61)],
        crs="EPSG:4326",
    )
    src = tmp_path / "points.gpkg"
    gdf.to_file(src, driver="GPKG")

    out = io.read_geo(src)

    assert out.crs.to_epsg() == 2927
    assert len(out) == 2


def test_read_geo_honors_target_crs_override(tmp_path):
    gdf = gpd.GeoDataFrame(
        {"id": [1]},
        geometry=[Point(-122.33, 47.61)],
        crs="EPSG:4326",
    )
    src = tmp_path / "points.gpkg"
    gdf.to_file(src, driver="GPKG")

    out = io.read_geo(src, target_crs="EPSG:3857")

    assert out.crs.to_epsg() == 3857


def test_read_geo_raises_when_source_has_no_crs(tmp_path, monkeypatch):
    src = tmp_path / "fake.gpkg"
    src.write_bytes(b"")

    def fake_read_file(_path):
        return gpd.GeoDataFrame({"id": [1]}, geometry=[Point(0, 0)], crs=None)

    monkeypatch.setattr(gpd, "read_file", fake_read_file)

    with pytest.raises(ValueError, match="no CRS"):
        io.read_geo(src)
