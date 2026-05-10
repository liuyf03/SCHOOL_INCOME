"""End-to-end synthetic test for build_dataset.main().

Builds a tiny in-memory dataset on disk, monkeypatches RAW_FILES and
PROCESSED_FILES, runs main(), and asserts both JSON outputs are written
and pass the Phase 3 schema validators.

Marked ``slow`` because writing 7 small geospatial files + running the
full pipeline takes a few seconds — outside the default unit-test
budget.
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon

from pipeline import build_dataset, io
from pipeline.brackets import BRACKET_COLUMNS

# Reuse Phase 3 schema validators
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))
from test_fixture_schema import (  # noqa: E402
    ALLOWED_REASONS,
    REQUIRED_FIELDS,
    SEARCH_INDEX_FIELDS,
)


def _square(x0, y0, side):
    return Polygon(
        [(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)]
    )


@pytest.mark.slow
def test_main_runs_end_to_end_on_synthetic_dataset(tmp_path, monkeypatch):
    raw_dir = tmp_path / "raw"
    proc_dir = tmp_path / "processed"
    raw_dir.mkdir()
    proc_dir.mkdir()

    # Two block groups in WA, side by side. EPSG:2927 = WA State Plane South.
    bgs = gpd.GeoDataFrame(
        {
            "GEOID": ["530001000001", "530001000002"],
            "STATEFP": ["53", "53"],
        },
        geometry=[_square(0, 0, 100), _square(100, 0, 100)],
        crs="EPSG:2927",
    )

    # 4 blocks evenly populated within each BG.
    blocks = gpd.GeoDataFrame(
        {
            "GEOID20": [f"BLOCK_{i}" for i in range(8)],
            "POP20": [50] * 8,
        },
        geometry=[
            _square(0, 0, 50), _square(50, 0, 50),
            _square(0, 50, 50), _square(50, 50, 50),
            _square(100, 0, 50), _square(150, 0, 50),
            _square(100, 50, 50), _square(150, 50, 50),
        ],
        crs="EPSG:2927",
    )

    # 3 SABS zones — one per BG plus a third that overlaps both
    sabs = gpd.GeoDataFrame(
        {
            "ncessch": ["530000001001", "530000002001", "530000003001"],
            "stAbbrev": ["WA", "WA", "WA"],
        },
        geometry=[
            _square(0, 0, 100),       # exactly BG 1
            _square(100, 0, 100),     # exactly BG 2
            _square(50, 0, 100),      # spans both
        ],
        crs="EPSG:2927",
    )

    # ACS B19131: 100 families per BG concentrated in the <$10k bracket
    # (column 004) and another 100 in $50k-$60k (col 013).
    b19131_data = {"GEOID": ["530001000001", "530001000002"]}
    for cols in BRACKET_COLUMNS:
        for col in cols:
            b19131_data[col] = [0.0, 0.0]
    # 300 families/BG in <$10k bracket (100 per family-type cell)
    for col in BRACKET_COLUMNS[0]:
        b19131_data[col] = [100.0, 100.0]
    # 300 families/BG in $50k-$60k bracket
    for col in BRACKET_COLUMNS[9]:
        b19131_data[col] = [100.0, 100.0]
    b19131 = pd.DataFrame(b19131_data)

    b11005 = pd.DataFrame(
        {"GEOID": ["530001000001", "530001000002"], "B11005_001E": [600, 600]}
    )
    b19013 = pd.DataFrame(
        {"GEOID": ["530001000001", "530001000002"], "B19013_001E": [55000, 55000]}
    )

    # CCD covers 4 schools — 3 with SABS polygons + 1 missing
    ccd = pd.DataFrame(
        {
            "NCESSCH": ["530000001001", "530000002001", "530000003001", "530000099001"],
            "SCH_NAME": ["Alpha HS", "Beta MS", "Gamma ES", "Orphan ES"],
            "LEA_NAME": ["Test SD"] * 4,
            "LSTATE": ["WA"] * 4,
            "LCITY": ["Testville"] * 4,
            "LSTREET1": ["1 Main St"] * 4,
            "LZIP": ["99999"] * 4,
            "GSLO": ["09", "06", "KG", "KG"],
            "GSHI": ["12", "08", "05", "05"],
        }
    )

    # Persist to disk so io.read_geo / pd.read_parquet work in main()
    raw_files = {
        "sabs": raw_dir / "sabs.gpkg",
        "block_groups": raw_dir / "bgs.gpkg",
        "blocks": raw_dir / "blocks.gpkg",
        "ccd": raw_dir / "ccd.parquet",
        "acs_b19131": raw_dir / "b19131.parquet",
        "acs_b11005": raw_dir / "b11005.parquet",
        "acs_b19013": raw_dir / "b19013.parquet",
    }
    sabs.to_file(raw_files["sabs"], driver="GPKG")
    bgs.to_file(raw_files["block_groups"], driver="GPKG")
    blocks.to_file(raw_files["blocks"], driver="GPKG")
    ccd.to_parquet(raw_files["ccd"])
    b19131.to_parquet(raw_files["acs_b19131"])
    b11005.to_parquet(raw_files["acs_b11005"])
    b19013.to_parquet(raw_files["acs_b19013"])

    processed_files = {
        "schools": proc_dir / "schools_wa.json",
        "search_index": proc_dir / "search_index.json",
    }

    monkeypatch.setattr(io, "RAW_FILES", raw_files)
    monkeypatch.setattr(io, "PROCESSED_FILES", processed_files)
    monkeypatch.setattr(build_dataset, "io", io)

    rc = build_dataset.main()

    assert rc == 0
    assert processed_files["schools"].exists()
    assert processed_files["search_index"].exists()

    schools = json.loads(processed_files["schools"].read_text(encoding="utf-8"))
    search_index = json.loads(
        processed_files["search_index"].read_text(encoding="utf-8")
    )

    # All 4 CCD schools present, including orphan
    assert set(schools.keys()) == {
        "530000001001", "530000002001", "530000003001", "530000099001"
    }

    # Orphan must be low-confidence with missing_sabs reason
    orphan = schools["530000099001"]
    assert orphan["low_confidence"] is True
    assert "missing_sabs" in orphan["low_confidence_reasons"]

    # Schema enforcement (Phase 3 validators)
    for nces_id, record in schools.items():
        assert REQUIRED_FIELDS <= record.keys()
        assert record["nces_id"] == nces_id
        for r in record["low_confidence_reasons"]:
            assert r in ALLOWED_REASONS

    # Search index conforms
    assert len(search_index) == len(schools)
    for entry in search_index:
        assert set(entry.keys()) == SEARCH_INDEX_FIELDS
        assert entry["nces_id"] in schools

    # The matched schools should have positive interpolated counts
    matched = schools["530000001001"]
    assert matched["total_families_with_children"] > 0
    assert matched["bracket_histogram"][0]["count"] > 0  # <$10k bracket populated
