"""Tests for serialize_schools — the JSON-shaping layer.

Reuses the schema validators from tests/site/test_fixture_schema.py
unchanged, so the contract enforced for the hand-authored Phase 3
fixture also holds for generated payloads.
"""
import json
import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon

from pipeline.brackets import BRACKET_COLUMNS
from pipeline.build_dataset import serialize_schools

# Reuse Phase 3 schema validators directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "site"))
from test_fixture_schema import (  # noqa: E402
    ALLOWED_REASONS,
    REQUIRED_FIELDS,
    SEARCH_INDEX_FIELDS,
)


def _ccd_row(nces_id, name="Test School", district="Test District",
             city="Seattle", state="WA", street="123 Main St", zip_="98101",
             gslo="KG", gshi="05"):
    return {
        "NCESSCH": nces_id,
        "SCH_NAME": name,
        "LEA_NAME": district,
        "LCITY": city,
        "LSTATE": state,
        "LSTREET1": street,
        "LZIP": zip_,
        "GSLO": gslo,
        "GSHI": gshi,
    }


def _zone_with_brackets(per_bracket: float):
    """A zone-like row with bracket counts evenly distributed across
    family-type columns."""
    record = {}
    for cols in BRACKET_COLUMNS:
        for col in cols:
            record[col] = per_bracket / 3
    return record


def test_payload_matches_schema():
    """Generated schools_wa.json conforms to docs/schema.md — the same
    validators that gate the Phase 3 fixture."""
    p1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    p2 = Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])
    rows = [
        # Healthy school
        {**_ccd_row("530000001001", name="Garfield"), **_zone_with_brackets(50),
         "geometry": p1},
        # Low-household-count school
        {**_ccd_row("530000002001", name="Tiny School"), **_zone_with_brackets(1),
         "geometry": p2},
        # Missing-SABS school (no zone data, null geometry)
        {**_ccd_row("530000003001", name="No Zone"), "geometry": None},
    ]

    schools, _ = serialize_schools(rows)

    assert isinstance(schools, dict)
    assert len(schools) == 3

    for nces_id, record in schools.items():
        missing = REQUIRED_FIELDS - record.keys()
        assert not missing, f"{nces_id} missing fields: {missing}"
        assert record["nces_id"] == nces_id
        # Histogram well-formed
        hist = record["bracket_histogram"]
        assert isinstance(hist, list) and hist
        for i, bucket in enumerate(hist):
            assert {"label", "lower", "upper", "count"} <= bucket.keys()
            assert bucket["count"] >= 0
            if bucket["upper"] is None:
                assert i == len(hist) - 1
            else:
                assert bucket["lower"] < bucket["upper"]
        # Reasons valid
        for r in record["low_confidence_reasons"]:
            assert r in ALLOWED_REASONS


def test_search_index_is_slim_subset():
    """search_index.json entries contain exactly {nces_id, name,
    district, city} — no extras."""
    rows = [
        {**_ccd_row("A"), **_zone_with_brackets(50),
         "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])},
        {**_ccd_row("B"), **_zone_with_brackets(50),
         "geometry": Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])},
    ]

    _, search_index = serialize_schools(rows)

    assert isinstance(search_index, list)
    for entry in search_index:
        assert set(entry.keys()) == SEARCH_INDEX_FIELDS


def test_search_index_keys_match_schools():
    rows = [
        {**_ccd_row("A"), "geometry": None},
        {**_ccd_row("B"), "geometry": None},
        {**_ccd_row("C"), "geometry": None},
    ]
    schools, search_index = serialize_schools(rows)

    nces_ids_in_index = {e["nces_id"] for e in search_index}
    assert nces_ids_in_index == set(schools.keys())


def test_search_index_sorted_by_nces_id():
    rows = [
        {**_ccd_row("530000003001"), "geometry": None},
        {**_ccd_row("530000001001"), "geometry": None},
        {**_ccd_row("530000002001"), "geometry": None},
    ]
    _, search_index = serialize_schools(rows)

    ids = [e["nces_id"] for e in search_index]
    assert ids == sorted(ids)


def test_skips_rows_with_blank_nces_id():
    rows = [
        {**_ccd_row("A"), "geometry": None},
        {**_ccd_row(""), "geometry": None},   # blank — should be skipped
        {**_ccd_row(None), "geometry": None},  # None — should be skipped
    ]
    schools, _ = serialize_schools(rows)
    assert set(schools.keys()) == {"A"}


def test_low_confidence_flag_set_for_missing_sabs_records():
    """Schools without a polygon are flagged via missing_sabs."""
    rows = [{**_ccd_row("X"), "geometry": None}]
    schools, _ = serialize_schools(rows)
    assert schools["X"]["low_confidence"] is True
    assert "missing_sabs" in schools["X"]["low_confidence_reasons"]


def test_grades_and_address_formatting():
    rows = [
        {
            **_ccd_row("X", gslo="KG", gshi="05",
                       street="400 23rd Ave", city="Seattle",
                       state="WA", zip_="98122"),
            "geometry": None,
        },
    ]
    schools, _ = serialize_schools(rows)
    assert schools["X"]["grades"] == "KG-05"
    assert schools["X"]["address"] == "400 23rd Ave, Seattle, WA 98122"


def test_output_size_under_5mb_for_synthetic_full_state():
    """A synthetic ~2,400-school payload must serialize under the 5 MB
    target documented in CLAUDE.md."""
    rows = []
    for i in range(2400):
        rows.append({
            **_ccd_row(f"5300000{i:05d}", name=f"Synthetic School {i}"),
            **_zone_with_brackets(100),
            "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        })
    schools, search_index = serialize_schools(rows)

    # Production uses the compact io.write_json variant for the multi-MB
    # full payload — measure that, not the pretty-printed form.
    payload = json.dumps(
        schools, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert len(payload) < 5 * 1024 * 1024, (
        f"schools_wa.json projected size {len(payload):,} bytes exceeds 5 MB target"
    )
    # Search index should be tiny
    idx_payload = json.dumps(search_index).encode("utf-8")
    assert len(idx_payload) < 500_000


def test_skips_empty_sabs_geometry_via_missing_sabs_flag():
    from shapely.geometry import Polygon as _P
    rows = [{**_ccd_row("X"), "geometry": _P()}]  # empty polygon
    schools, _ = serialize_schools(rows)
    assert schools["X"]["low_confidence"] is True
    assert "missing_sabs" in schools["X"]["low_confidence_reasons"]
