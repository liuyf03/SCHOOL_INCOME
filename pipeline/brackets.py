"""Frozen mapping of B19131 'with own children' columns to income brackets.

B19131 cross-tabs Family Type x Presence of Own Children x Income Bracket.
For 'households with school-aged children' we want only the cells where the
householder has own children under 18, summed across the three family
types (married-couple, male-householder no spouse, female-householder no
spouse).

The 16 income brackets line up exactly with $35k and $150k, so
share_under_35k and share_over_150k can be computed without prorating
across bracket boundaries — those are bracket edges by design.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bracket:
    label: str
    lower: int
    upper: int | None  # None for the top open-ended bracket


# Listed in income order. The 16 cut-points are the ACS B19131 bracket
# boundaries. Note $35k and $150k are explicit edges.
BRACKETS: tuple[Bracket, ...] = (
    Bracket("<$10k",        0,      10000),
    Bracket("$10k-$15k",    10000,  15000),
    Bracket("$15k-$20k",    15000,  20000),
    Bracket("$20k-$25k",    20000,  25000),
    Bracket("$25k-$30k",    25000,  30000),
    Bracket("$30k-$35k",    30000,  35000),
    Bracket("$35k-$40k",    35000,  40000),
    Bracket("$40k-$45k",    40000,  45000),
    Bracket("$45k-$50k",    45000,  50000),
    Bracket("$50k-$60k",    50000,  60000),
    Bracket("$60k-$75k",    60000,  75000),
    Bracket("$75k-$100k",   75000,  100000),
    Bracket("$100k-$125k",  100000, 125000),
    Bracket("$125k-$150k",  125000, 150000),
    Bracket("$150k-$200k",  150000, 200000),
    Bracket("$200k+",       200000, None),
)

SHARE_UNDER_35K_CUTOFF = 35000
SHARE_OVER_150K_CUTOFF = 150000

# B19131 bracket-column ranges per family-type / with-children cohort:
#   - Married-couple, with own children: 004..019 (16 cols)
#   - Male householder no spouse, with own children: 040..055
#   - Female householder no spouse, with own children: 075..090
# (See https://api.census.gov/data/2022/acs/acs5/groups/B19131.json)
_MARRIED_WITH_KIDS = tuple(f"B19131_{n:03d}E" for n in range(4, 20))
_MALE_HH_WITH_KIDS = tuple(f"B19131_{n:03d}E" for n in range(40, 56))
_FEMALE_HH_WITH_KIDS = tuple(f"B19131_{n:03d}E" for n in range(75, 91))

# For each of the 16 income brackets, the trio of B19131 columns
# (married, male-hh, female-hh) summed to total families-with-children.
BRACKET_COLUMNS: tuple[tuple[str, str, str], ...] = tuple(
    zip(_MARRIED_WITH_KIDS, _MALE_HH_WITH_KIDS, _FEMALE_HH_WITH_KIDS)
)
assert len(BRACKETS) == len(BRACKET_COLUMNS) == 16


def families_with_children_columns() -> tuple[str, ...]:
    """All B19131 columns we sum to get total families-with-children counts."""
    return tuple(c for trio in BRACKET_COLUMNS for c in trio)
