"""Tests for the frozen B19131 bracket mapping."""
from pipeline.brackets import (
    BRACKETS,
    BRACKET_COLUMNS,
    SHARE_OVER_150K_CUTOFF,
    SHARE_UNDER_35K_CUTOFF,
    families_with_children_columns,
)


def test_brackets_contiguous_and_open_at_top():
    """Brackets must be contiguous, non-overlapping, with only the last
    bracket open-ended."""
    assert BRACKETS[0].lower == 0
    assert BRACKETS[-1].upper is None
    for prev, curr in zip(BRACKETS, BRACKETS[1:]):
        assert prev.upper == curr.lower, (
            f"gap or overlap between {prev.label} and {curr.label}"
        )
    for bracket in BRACKETS[:-1]:
        assert bracket.upper is not None
        assert bracket.lower < bracket.upper


def test_share_cutoffs_align_with_bracket_edges():
    """share_under_35k / share_over_150k must be summable without
    prorating across brackets."""
    edges = {b.lower for b in BRACKETS}
    edges.add(BRACKETS[-1].upper or 0)
    edges.update(b.upper for b in BRACKETS if b.upper is not None)
    assert SHARE_UNDER_35K_CUTOFF in edges
    assert SHARE_OVER_150K_CUTOFF in edges


def test_bracket_columns_have_three_family_types_per_bracket():
    for cols in BRACKET_COLUMNS:
        assert len(cols) == 3, f"expected 3 family-type cells, got {cols}"


def test_bracket_columns_match_expected_b19131_codes():
    """First col is married, second male-hh, third female-hh."""
    for i, (married, male_hh, female_hh) in enumerate(BRACKET_COLUMNS):
        assert married == f"B19131_{4 + i:03d}E"
        assert male_hh == f"B19131_{40 + i:03d}E"
        assert female_hh == f"B19131_{75 + i:03d}E"


def test_families_with_children_columns_unique_and_complete():
    cols = families_with_children_columns()
    assert len(cols) == 16 * 3
    assert len(set(cols)) == len(cols)
