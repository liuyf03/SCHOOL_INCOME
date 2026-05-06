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


def test_fetch_uses_local_archive_when_present(tmp_path, monkeypatch):
    """A manually-downloaded SABS_1516.zip at LOCAL_ARCHIVE must short-circuit
    the network call — that's the documented escape hatch when NCES is flaky."""
    target = tmp_path / "sabs.gpkg"
    local_zip = tmp_path / "SABS_1516.zip"
    local_zip.write_bytes(b"sentinel")
    monkeypatch.setitem(sabs.RAW_FILES, "sabs", target)
    monkeypatch.setattr(sabs, "LOCAL_ARCHIVE", local_zip)

    captured = []
    monkeypatch.setattr(sabs, "_convert_zip_to_gpkg", lambda zip_path, out: captured.append((zip_path, out)))

    def fail_if_called(*a, **k):
        raise AssertionError("network call attempted despite local archive")

    monkeypatch.setattr(sabs, "_stream_to_disk", fail_if_called)

    results = sabs.fetch(force=False)

    assert results == [("SABS", "converted-local", target)]
    assert captured == [(local_zip, target)]


def test_session_has_browser_user_agent_and_retry_policy():
    s = sabs._session()
    assert "Mozilla" in s.headers["User-Agent"]
    adapter = s.get_adapter("https://nces.ed.gov/")
    assert adapter.max_retries.total == sabs.MAX_RETRIES
    assert 503 in adapter.max_retries.status_forcelist
