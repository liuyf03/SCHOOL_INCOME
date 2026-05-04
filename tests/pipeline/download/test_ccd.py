"""Tests for the NCES CCD downloader."""
import pandas as pd
import pytest

from pipeline.download import ccd


def test_filter_to_wa_keeps_only_wa_rows(fixtures_dir):
    df = pd.read_csv(
        fixtures_dir / "ccd_sample.csv", dtype={"NCESSCH": str, "LZIP": str}
    )
    out = ccd.filter_to_wa(df)

    # Fixture has 3 active WA + 1 closed WA + 1 CA. Filter is state-only,
    # not status-aware (closed schools still belong to WA).
    assert len(out) == 4
    assert set(out["LSTATE"]) == {"WA"}
    assert "Hollywood High" not in out["SCH_NAME"].values


def test_filter_to_wa_preserves_required_columns(fixtures_dir):
    df = pd.read_csv(fixtures_dir / "ccd_sample.csv", dtype={"NCESSCH": str})
    out = ccd.filter_to_wa(df)
    for col in ccd.CCD_REQUIRED_COLUMNS:
        assert col in out.columns


def test_filter_to_wa_raises_on_missing_required_column():
    df = pd.DataFrame({"NCESSCH": ["530000001001"], "SCH_NAME": ["x"]})
    with pytest.raises(ValueError, match="missing required columns"):
        ccd.filter_to_wa(df)


def test_fetch_skips_when_output_exists(tmp_path, monkeypatch):
    target = tmp_path / "ccd.parquet"
    target.write_bytes(b"x")
    monkeypatch.setitem(ccd.RAW_FILES, "ccd", target)

    results = ccd.fetch(force=False)

    assert results == [("CCD", "skipped", target)]
