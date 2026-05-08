"""Tests for the SABS downloader."""
import io as stdio
import re
import zipfile

import geopandas as gpd
import pytest
import responses
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
    with pytest.raises(ValueError, match="no recognized state column"):
        sabs.filter_to_wa(gdf)


def test_filter_to_wa_uses_lstate_alpha_column():
    gdf = gpd.GeoDataFrame(
        {"LSTATE": ["WA", "CA", "WA"], "n": [1, 2, 3]},
        geometry=[Point(0, i) for i in range(3)],
        crs="EPSG:4326",
    )
    assert len(sabs.filter_to_wa(gdf)) == 2


def test_filter_to_wa_uses_lowercase_lstate():
    gdf = gpd.GeoDataFrame(
        {"lstate": ["wa", "ca", "WA"], "n": [1, 2, 3]},
        geometry=[Point(0, i) for i in range(3)],
        crs="EPSG:4326",
    )
    assert len(sabs.filter_to_wa(gdf)) == 2


def test_filter_to_wa_falls_back_to_leaid_prefix():
    gdf = gpd.GeoDataFrame(
        {"leaid": ["5300001", "0600001", "5300002"], "n": [1, 2, 3]},
        geometry=[Point(0, i) for i in range(3)],
        crs="EPSG:4326",
    )
    assert len(sabs.filter_to_wa(gdf)) == 2


def test_filter_to_wa_prefers_numeric_state_over_leaid():
    """When both a state column and leaid are present, the explicit state
    column wins — leaid is the last-resort fallback only."""
    gdf = gpd.GeoDataFrame(
        {"STATEFP": ["53", "06"], "leaid": ["5300001", "5300002"], "n": [1, 2]},
        geometry=[Point(0, 0), Point(0, 1)],
        crs="EPSG:4326",
    )
    out = sabs.filter_to_wa(gdf)
    assert len(out) == 1
    assert out["leaid"].iloc[0] == "5300001"


def test_fetch_skips_when_output_exists(tmp_path, monkeypatch):
    target = tmp_path / "sabs.gpkg"
    target.write_bytes(b"already here")
    monkeypatch.setitem(sabs.RAW_FILES, "sabs", target)

    results = sabs.fetch(force=False)

    assert results == [("SABS", "skipped", target)]


def test_fetch_uses_local_archive_when_present(tmp_path, monkeypatch):
    """A valid manually-downloaded SABS_1516.zip at LOCAL_ARCHIVE must
    short-circuit the network call — that's the escape hatch when NCES
    is flaky."""
    target = tmp_path / "sabs.gpkg"
    local_zip = tmp_path / "SABS_1516.zip"
    local_zip.write_bytes(_make_zip_bytes({"sabs.shp": b"x", "sabs.dbf": b"y"}))
    monkeypatch.setitem(sabs.RAW_FILES, "sabs", target)
    monkeypatch.setattr(sabs, "LOCAL_ARCHIVE", local_zip)

    captured = []
    monkeypatch.setattr(sabs, "_convert_zip_to_gpkg", lambda zip_path, out: captured.append((zip_path, out)))

    def fail_if_called(*a, **k):
        raise AssertionError("network call attempted despite local archive")

    monkeypatch.setattr(sabs, "_stream_with_resume", fail_if_called)
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


def _make_zip_bytes(contents: dict[str, bytes]) -> bytes:
    """Build an in-memory zip with the given files."""
    buf = stdio.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in contents.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_archive_is_valid_accepts_zip_with_shp(tmp_path):
    z = tmp_path / "ok.zip"
    z.write_bytes(_make_zip_bytes({"a.shp": b"x", "a.dbf": b"y"}))
    assert sabs._archive_is_valid(z) is True


def test_archive_is_valid_rejects_truncated_zip(tmp_path):
    z = tmp_path / "bad.zip"
    z.write_bytes(_make_zip_bytes({"a.shp": b"x"})[:50])  # truncated
    assert sabs._archive_is_valid(z) is False


def test_archive_is_valid_rejects_zip_without_shp(tmp_path):
    z = tmp_path / "no-shp.zip"
    z.write_bytes(_make_zip_bytes({"readme.txt": b"hi"}))
    assert sabs._archive_is_valid(z) is False


def test_archive_is_valid_rejects_missing_file(tmp_path):
    assert sabs._archive_is_valid(tmp_path / "nope.zip") is False


@responses.activate
def test_stream_to_disk_sends_range_header_when_partial_exists(tmp_path):
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"ABCDEFGHIJ")  # 10 bytes already

    responses.add(
        responses.GET, re.compile(r".*"),
        body=b"KLMNOP", status=206,
    )

    sabs._stream_to_disk("https://example.com/x", dest, resume=True)

    assert dest.read_bytes() == b"ABCDEFGHIJKLMNOP"
    assert responses.calls[0].request.headers.get("Range") == "bytes=10-"


@responses.activate
def test_stream_to_disk_truncates_when_server_ignores_range(tmp_path):
    """If the server replies 200 instead of 206, it sent the whole file —
    we must overwrite, not append, or we'd produce a corrupted concat."""
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"OLD-PARTIAL")

    responses.add(
        responses.GET, re.compile(r".*"),
        body=b"FULL_FILE_BYTES", status=200,
    )

    sabs._stream_to_disk("https://example.com/x", dest, resume=True)

    assert dest.read_bytes() == b"FULL_FILE_BYTES"


@responses.activate
def test_stream_to_disk_skips_when_416(tmp_path):
    """416 Range Not Satisfiable means the partial covers the whole file."""
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"already complete")
    responses.add(responses.GET, re.compile(r".*"), status=416)

    sabs._stream_to_disk("https://example.com/x", dest, resume=True)

    assert dest.read_bytes() == b"already complete"


@responses.activate
def test_stream_with_resume_picks_up_after_failure(tmp_path):
    """Drop the first attempt mid-stream; resume must pick up via Range."""
    import requests as _r

    dest = tmp_path / "out.bin"
    dest.write_bytes(b"FIRST")  # 5 bytes already written by an earlier attempt

    responses.add(
        responses.GET, re.compile(r".*"),
        body=_r.exceptions.ConnectionError("simulated drop"),
    )
    responses.add(
        responses.GET, re.compile(r".*"),
        body=b"SECOND",
        status=206,
    )

    sabs._stream_with_resume("https://example.com/x", dest, max_attempts=3)

    assert dest.read_bytes() == b"FIRSTSECOND"


def test_stream_with_resume_aborts_when_no_progress(tmp_path, monkeypatch):
    """Repeated failures with no bytes appended must surface as an error
    rather than spinning forever."""
    dest = tmp_path / "out.bin"
    dest.write_bytes(b"X" * 100)

    import requests as _r

    def always_fails(*a, **k):
        raise _r.exceptions.ConnectionError("nope")

    monkeypatch.setattr(sabs, "_stream_to_disk", always_fails)

    with pytest.raises(RuntimeError, match="stalled"):
        sabs._stream_with_resume("https://example.com/x", dest, max_attempts=3)
