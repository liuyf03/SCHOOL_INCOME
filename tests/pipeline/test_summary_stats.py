"""Tests for compute_summary_stats — median, shares, and histogram."""
from pipeline.brackets import BRACKET_COLUMNS, BRACKETS
from pipeline.build_dataset import compute_summary_stats


def _record_with_evenly_split_brackets(per_bracket: float) -> dict:
    """Build a record where every bracket has ``per_bracket`` total counts,
    split evenly across the three family-type columns (married, male-hh,
    female-hh)."""
    record = {}
    for cols in BRACKET_COLUMNS:
        for col in cols:
            record[col] = per_bracket / 3.0
    return record


def test_evenly_distributed_record_has_median_at_middle_bracket():
    record = _record_with_evenly_split_brackets(10)

    out = compute_summary_stats(record)

    # 16 brackets × 10 = 160 total, half = 80 → median is in 8th bracket
    # ($40k-$45k). Linear interpolation lands at the bracket midpoint.
    assert out["total_families_with_children"] == 160
    assert 40000 <= out["median_family_income"] <= 45000


def test_median_within_500_of_hand_computed_value():
    """Hand-computed: 50 families <$10k, 100 in $50k-$60k, 50 in $200k+
    (total 200, half = 100). The first 50 fall in <$10k; the next 50
    needed to reach the median land halfway through the $50k-$60k bracket
    by linear interpolation → median = 50000 + 0.5*(60000-50000) = 55000."""
    record = {col: 0.0 for cols in BRACKET_COLUMNS for col in cols}
    for col in BRACKET_COLUMNS[0]:    # <$10k
        record[col] = 50 / 3
    for col in BRACKET_COLUMNS[9]:    # $50k-$60k
        record[col] = 100 / 3
    for col in BRACKET_COLUMNS[15]:   # $200k+
        record[col] = 50 / 3

    out = compute_summary_stats(record)

    assert abs(out["median_family_income"] - 55000) <= 500


def test_median_lands_at_bracket_boundary_when_cumulative_hits_half_exactly():
    """If the cumulative count reaches exactly half at the top of a bracket,
    the median is that bracket's upper edge — half of households earn at
    or below that value."""
    record = {col: 0.0 for cols in BRACKET_COLUMNS for col in cols}
    for col in BRACKET_COLUMNS[0]:    # <$10k
        record[col] = 100 / 3
    for col in BRACKET_COLUMNS[9]:    # $50k-$60k
        record[col] = 100 / 3

    out = compute_summary_stats(record)

    assert out["median_family_income"] == 10000


def test_share_under_35k_computed_correctly():
    """40 families in <$10k, 60 families in $200k+ → share_under_35k = 0.4."""
    record = {}
    for col in BRACKET_COLUMNS[0]:    # <$10k
        record[col] = 40 / 3
    for col in BRACKET_COLUMNS[15]:   # $200k+
        record[col] = 60 / 3

    out = compute_summary_stats(record)

    assert abs(out["share_under_35k"] - 0.4) < 1e-6
    assert abs(out["share_over_150k"] - 0.6) < 1e-6


def test_share_over_150k_includes_open_ended_top_bracket():
    """The $200k+ bracket has upper=None and must still count toward
    share_over_150k since lower=200000 > cutoff."""
    record = {}
    for col in BRACKET_COLUMNS[15]:   # $200k+
        record[col] = 30
    for col in BRACKET_COLUMNS[14]:   # $150k-$200k
        record[col] = 30
    for col in BRACKET_COLUMNS[0]:    # <$10k
        record[col] = 40

    out = compute_summary_stats(record)

    # 60 of 100 are over $150k (30 in $150-200k + 30 in $200k+).
    assert abs(out["share_over_150k"] - 0.6) < 1e-6


def test_zero_total_returns_nulls_not_nan():
    """Empty zones (e.g. missing SABS polygon) must not propagate NaN."""
    record = {col: 0 for cols in BRACKET_COLUMNS for col in cols}

    out = compute_summary_stats(record)

    assert out["median_family_income"] is None
    assert out["share_under_35k"] is None
    assert out["share_over_150k"] is None
    assert out["total_families_with_children"] == 0


def test_histogram_has_one_entry_per_bracket():
    record = _record_with_evenly_split_brackets(5)

    out = compute_summary_stats(record)

    assert len(out["bracket_histogram"]) == len(BRACKETS)
    for entry, bracket in zip(out["bracket_histogram"], BRACKETS):
        assert entry["label"] == bracket.label
        assert entry["lower"] == bracket.lower
        assert entry["upper"] == bracket.upper
        assert entry["count"] == 5


def test_nan_counts_treated_as_zero():
    """ACS suppresses small-cell counts as NaN. compute_summary_stats
    must not propagate NaN through the total or the median."""
    import math
    record = {col: math.nan for cols in BRACKET_COLUMNS for col in cols}

    out = compute_summary_stats(record)

    assert out["total_families_with_children"] == 0
    assert out["median_family_income"] is None
    assert out["share_under_35k"] is None
    assert all(entry["count"] == 0 for entry in out["bracket_histogram"])


def test_missing_columns_treated_as_zero():
    """A real interpolated record might be missing some B19131 columns
    (e.g. the ACS API suppressed the value). Treat them as zero."""
    record = {col: 1 for col in BRACKET_COLUMNS[0]}  # only the <$10k bracket
    # Other 15 brackets: columns absent entirely

    out = compute_summary_stats(record)

    assert out["total_families_with_children"] == 3
    assert out["bracket_histogram"][0]["count"] == 3
    assert out["bracket_histogram"][1]["count"] == 0


def test_open_top_bracket_returns_lower_bound_when_median_lands_there():
    """When the median falls in the $200k+ bracket (no upper bound), we
    can't linearly interpolate inside it — return the bracket's lower
    bound rather than fabricating a value."""
    record = {col: 0 for cols in BRACKET_COLUMNS for col in cols}
    for col in BRACKET_COLUMNS[15]:   # $200k+
        record[col] = 50

    out = compute_summary_stats(record)

    assert out["median_family_income"] == 200000
