"""Tests for flag_low_confidence."""
from shapely.geometry import Polygon

from pipeline.build_dataset import (
    LOW_HOUSEHOLD_THRESHOLD,
    REASON_LOW_HOUSEHOLD,
    REASON_MISSING_SABS,
    flag_low_confidence,
)


def _ok_polygon():
    return Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])


def test_healthy_record_not_flagged():
    record = {
        "geometry": _ok_polygon(),
        "total_families_with_children": LOW_HOUSEHOLD_THRESHOLD * 10,
    }
    flag, reasons = flag_low_confidence(record)
    assert flag is False
    assert reasons == []


def test_missing_geometry_flags_missing_sabs():
    record = {"geometry": None, "total_families_with_children": 1000}
    flag, reasons = flag_low_confidence(record)
    assert flag is True
    assert REASON_MISSING_SABS in reasons


def test_empty_geometry_flags_missing_sabs():
    record = {
        "geometry": Polygon(),  # empty polygon
        "total_families_with_children": 1000,
    }
    flag, reasons = flag_low_confidence(record)
    assert flag is True
    assert REASON_MISSING_SABS in reasons


def test_low_count_flags_low_household():
    record = {
        "geometry": _ok_polygon(),
        "total_families_with_children": LOW_HOUSEHOLD_THRESHOLD - 1,
    }
    flag, reasons = flag_low_confidence(record)
    assert flag is True
    assert REASON_LOW_HOUSEHOLD in reasons


def test_threshold_exact_value_not_flagged():
    """The threshold is strict-less-than: total == LOW_HOUSEHOLD_THRESHOLD
    must not be flagged."""
    record = {
        "geometry": _ok_polygon(),
        "total_families_with_children": LOW_HOUSEHOLD_THRESHOLD,
    }
    flag, _ = flag_low_confidence(record)
    assert flag is False


def test_both_reasons_combine():
    record = {"geometry": None, "total_families_with_children": 0}
    flag, reasons = flag_low_confidence(record)
    assert flag is True
    assert REASON_MISSING_SABS in reasons
    assert REASON_LOW_HOUSEHOLD in reasons


def test_none_total_treated_as_low():
    """A null total (no interpolation result at all) is also low-confidence."""
    record = {"geometry": _ok_polygon(), "total_families_with_children": None}
    flag, reasons = flag_low_confidence(record)
    assert flag is True
    assert REASON_LOW_HOUSEHOLD in reasons
