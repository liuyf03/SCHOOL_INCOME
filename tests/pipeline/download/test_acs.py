"""Tests for the Census ACS downloader."""
import json
import re

import pandas as pd
import pytest
import responses

from pipeline import io
from pipeline.download import acs


def test_build_url_contains_table_state_geography():
    url = acs.build_url("B19131")
    assert "B19131" in url
    assert "state:53" in url
    assert "block%20group:*" in url
    assert str(io.ACS_VINTAGE) in url


def test_build_url_overrides():
    url = acs.build_url("B11005", vintage=2020, state="06")
    assert "/2020/" in url
    assert "state:06" in url
    assert "B11005" in url


def test_parse_response_builds_geoid_and_casts_numeric(fixtures_dir):
    rows = json.loads((fixtures_dir / "acs_b19131_sample.json").read_text())
    df = acs.parse_response(rows)

    assert len(df) == 3
    assert "GEOID" in df.columns
    assert df["GEOID"].iloc[0] == "530330001001"
    assert all(df["GEOID"].str.startswith("53"))
    assert pd.api.types.is_numeric_dtype(df["B19131_001E"])
    assert df["B19131_001E"].iloc[0] == 1234


def test_parse_response_raises_on_empty():
    with pytest.raises(ValueError, match="Empty"):
        acs.parse_response([])


def test_parse_response_raises_on_missing_geography_columns():
    rows = [["B19131_001E", "state"], ["100", "53"]]
    with pytest.raises(ValueError, match="geography columns"):
        acs.parse_response(rows)


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CENSUS_API_KEY"):
        acs._api_key()


def test_fetch_table_skips_when_exists(tmp_path, monkeypatch):
    target = tmp_path / "b19131.parquet"
    target.write_bytes(b"x")
    monkeypatch.setitem(acs.RAW_FILES, "acs_b19131", target)

    label, status, path = acs.fetch_table("B19131", force=False)

    assert (label, status, path) == ("ACS B19131", "skipped", target)


@responses.activate
def test_fetch_table_writes_parquet(tmp_path, monkeypatch, fixtures_dir):
    target = tmp_path / "b19131.parquet"
    monkeypatch.setitem(acs.RAW_FILES, "acs_b19131", target)
    monkeypatch.setenv("CENSUS_API_KEY", "fake-key")

    sample = json.loads((fixtures_dir / "acs_b19131_sample.json").read_text())
    responses.add(
        responses.GET,
        re.compile(r"https://api\.census\.gov/data/.*B19131.*"),
        json=sample,
        status=200,
    )

    label, status, path = acs.fetch_table("B19131", force=False)

    assert (label, status, path) == ("ACS B19131", "downloaded", target)
    assert target.exists()
    df = pd.read_parquet(target)
    assert len(df) == 3
    assert "GEOID" in df.columns
    assert df["GEOID"].iloc[0] == "530330001001"

    # Verify the request URL contained the expected query params
    request_url = responses.calls[0].request.url
    assert "B19131" in request_url
    assert "state:53" in request_url
    assert "key=fake-key" in request_url
