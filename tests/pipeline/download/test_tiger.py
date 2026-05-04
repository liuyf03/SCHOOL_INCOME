"""Tests for the TIGER downloader."""
from pipeline import io
from pipeline.download import tiger


def test_block_groups_url_targets_wa_for_acs_vintage():
    url = tiger.BLOCK_GROUPS_URL
    assert f"_{io.STATE_FIPS}_" in url
    assert str(io.ACS_VINTAGE) in url
    assert url.endswith("_bg.zip")


def test_blocks_url_uses_2020_tabblock_layer():
    url = tiger.BLOCKS_URL
    assert f"_{io.STATE_FIPS}_" in url
    assert "tabblock20" in url
    assert str(io.ACS_VINTAGE) in url


def test_fetch_block_groups_skips_when_exists(tmp_path, monkeypatch):
    target = tmp_path / "bg.gpkg"
    target.write_bytes(b"x")
    monkeypatch.setitem(tiger.RAW_FILES, "block_groups", target)

    label, status, path = tiger.fetch_block_groups(force=False)

    assert (label, status, path) == ("TIGER block groups", "skipped", target)


def test_fetch_blocks_skips_when_exists(tmp_path, monkeypatch):
    target = tmp_path / "blocks.gpkg"
    target.write_bytes(b"x")
    monkeypatch.setitem(tiger.RAW_FILES, "blocks", target)

    label, status, path = tiger.fetch_blocks(force=False)

    assert (label, status, path) == ("TIGER blocks", "skipped", target)


def test_fetch_returns_both_results(tmp_path, monkeypatch):
    bg = tmp_path / "bg.gpkg"
    blocks = tmp_path / "blocks.gpkg"
    bg.write_bytes(b"x")
    blocks.write_bytes(b"y")
    monkeypatch.setitem(tiger.RAW_FILES, "block_groups", bg)
    monkeypatch.setitem(tiger.RAW_FILES, "blocks", blocks)

    results = tiger.fetch(force=False)

    assert len(results) == 2
    assert {r[0] for r in results} == {"TIGER block groups", "TIGER blocks"}
    assert all(status == "skipped" for _, status, _ in results)
