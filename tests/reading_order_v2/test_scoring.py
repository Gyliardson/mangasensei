from __future__ import annotations

from fractions import Fraction

import pytest
from scripts.reading_order_v2.contracts import PageGroundTruth, QualificationPair
from scripts.reading_order_v2.scoring import (
    candidate_only_wrong_pairs,
    score_page,
    wrong_set_is_subset,
)


def _gt() -> PageGroundTruth:
    return PageGroundTruth(
        "H01",
        ("a", "b", "c"),
        ("noise",),
        (
            QualificationPair("qa", "a", "b", ("A", "horizontal-only")),
            QualificationPair("qb", "b", "c", ("B", "vertical-only")),
        ),
        ("clean-control",),
        (),
    )


def test_zero_one_and_full_reversal_inversions() -> None:
    correct = score_page(_gt(), ("a", "noise", "b", "c"))
    one = score_page(_gt(), ("b", "a", "noise", "c"))
    reverse = score_page(_gt(), ("noise", "c", "b", "a"))
    assert correct.aggregate.inversions == 0
    assert one.aggregate.wrong_pairs == (("a", "b"),)
    assert reverse.aggregate.inversions == 3
    assert reverse.aggregate.normalized_inversion_distance == Fraction(1, 1)
    assert one.observed_scored_order == ("b", "a", "c")


def test_scoring_rejects_duplicate_missing_or_unknown_regions() -> None:
    with pytest.raises(ValueError, match="observed ordering contains duplicate region IDs"):
        score_page(_gt(), ("a", "b", "b", "noise"))
    with pytest.raises(ValueError, match="observed region set mismatch"):
        score_page(_gt(), ("a", "b", "noise"))
    with pytest.raises(ValueError, match="observed region set mismatch"):
        score_page(_gt(), ("a", "b", "c", "noise", "other"))


def test_wrong_set_subset_surfaces_candidate_only_inversion() -> None:
    control = score_page(_gt(), ("b", "a", "noise", "c")).aggregate
    improved = score_page(_gt(), ("a", "noise", "b", "c")).aggregate
    changed = score_page(_gt(), ("a", "c", "noise", "b")).aggregate
    assert wrong_set_is_subset(control, improved)
    assert not wrong_set_is_subset(control, changed)
    assert candidate_only_wrong_pairs(control, changed) == (("b", "c"),)
