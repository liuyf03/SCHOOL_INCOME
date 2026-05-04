"""Schema validators for the processed JSON outputs.

These tests run against the Phase 3 hand-authored fixture and are reused
unchanged in Phase 6 against the generated payload — they encode the
JSON contract documented in docs/schema.md.
"""
import pytest

from pipeline import io

REQUIRED_FIELDS = {
    "nces_id",
    "name",
    "district",
    "city",
    "grades",
    "address",
    "median_family_income",
    "share_under_35k",
    "share_over_150k",
    "total_families_with_children",
    "bracket_histogram",
    "low_confidence",
    "low_confidence_reasons",
}

ALLOWED_REASONS = {"missing_sabs", "low_household_count"}

SEARCH_INDEX_FIELDS = {"nces_id", "name", "district", "city"}


@pytest.fixture(scope="module")
def schools():
    return io.read_json(io.PROCESSED_FILES["schools"])


@pytest.fixture(scope="module")
def search_index():
    return io.read_json(io.PROCESSED_FILES["search_index"])


def test_required_fields_present(schools):
    assert isinstance(schools, dict)
    assert len(schools) > 0
    for nces_id, record in schools.items():
        missing = REQUIRED_FIELDS - record.keys()
        assert not missing, f"{nces_id} missing fields: {missing}"
        assert record["nces_id"] == nces_id


def test_search_index_keys_match_schools(schools, search_index):
    assert isinstance(search_index, list)
    assert len(search_index) == len(schools)
    for entry in search_index:
        assert entry["nces_id"] in schools, f"orphan search-index entry: {entry['nces_id']}"


def test_search_index_slim_subset(search_index):
    for entry in search_index:
        assert set(entry.keys()) == SEARCH_INDEX_FIELDS, (
            f"unexpected search-index keys: {entry.keys()}"
        )


def test_low_confidence_records_have_reasons(schools):
    for nces_id, record in schools.items():
        if record["low_confidence"]:
            reasons = record["low_confidence_reasons"]
            assert reasons, f"{nces_id} flagged low-confidence but reasons list is empty"
            unknown = set(reasons) - ALLOWED_REASONS
            assert not unknown, f"{nces_id} has unknown reasons: {unknown}"
        else:
            assert record["low_confidence_reasons"] == [], (
                f"{nces_id} not low-confidence but has reasons"
            )


def test_bracket_histogram_well_formed(schools):
    for nces_id, record in schools.items():
        hist = record["bracket_histogram"]
        assert isinstance(hist, list) and hist, f"{nces_id} histogram empty/missing"
        for i, bucket in enumerate(hist):
            assert {"label", "lower", "upper", "count"} <= bucket.keys(), (
                f"{nces_id} bucket {i} missing fields: {bucket}"
            )
            assert bucket["count"] >= 0, f"{nces_id} bucket {i} negative count"
            assert isinstance(bucket["lower"], (int, float))
            if bucket["upper"] is None:
                assert i == len(hist) - 1, (
                    f"{nces_id} open-ended bucket must be last (got index {i})"
                )
            else:
                assert bucket["lower"] < bucket["upper"], (
                    f"{nces_id} bucket {i} has lower >= upper"
                )


def test_bracket_histogram_contiguous(schools):
    for nces_id, record in schools.items():
        hist = record["bracket_histogram"]
        for prev, curr in zip(hist, hist[1:]):
            assert prev["upper"] == curr["lower"], (
                f"{nces_id} brackets not contiguous between '{prev['label']}' and '{curr['label']}'"
            )


def test_both_low_confidence_reasons_observed(schools):
    """Sanity check: a complete dataset should have at least one record
    flagged for each reason. If none are flagged for a given reason, the
    flagging logic is likely not running."""
    seen = set()
    for record in schools.values():
        seen.update(record["low_confidence_reasons"])
    for reason in ALLOWED_REASONS:
        assert reason in seen, f"no record observed with low_confidence_reason={reason}"


def test_share_fields_in_unit_range_or_null(schools):
    for nces_id, record in schools.items():
        for field in ("share_under_35k", "share_over_150k"):
            value = record[field]
            if value is None:
                continue
            assert 0 <= value <= 1, f"{nces_id} {field}={value} out of [0, 1]"
