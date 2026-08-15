from __future__ import annotations

from scripts.reading_order_v2.contracts import (
    AnnotationPage,
    QualificationPair,
    RegionGroundTruth,
)
from scripts.reading_order_v2.scoring import (
    score_corpus,
    score_page,
    wrong_set_comparison,
)


def annotation(page_id: str = "H01") -> AnnotationPage:
    regions = tuple(
        RegionGroundTruth(
            region_id,
            scored,
            position,
            "horizontal",
            "not-applicable",
            None,
        )
        for region_id, scored, position in (
            ("a", True, 0),
            ("b", True, 1),
            ("c", True, 2),
            ("x", False, None),
        )
    )
    pairs = (
        QualificationPair("p1", "a", "b", ("A", "horizontal-only")),
        QualificationPair("p2", "b", "c", ("B", "horizontal-only")),
    )
    return AnnotationPage(
        page_id,
        100,
        100,
        "0" * 64,
        regions,
        ("a", "b", "c"),
        pairs,
        ("clean-control",),
        (),
        (),
    )


def test_zero_inversion_and_unscored_distractor_is_ignored() -> None:
    score = score_page(annotation(), ("a", "x", "b", "c"))
    assert score.inversion_count == 0
    assert score.exact_sequence is True
    assert score.comparable_pair_count == 3


def test_one_inversion_has_exact_canonical_wrong_pair() -> None:
    score = score_page(annotation(), ("b", "a", "c", "x"))
    assert score.inversion_count == 1
    assert score.wrong_pairs == ("H01:a>b",)


def test_complete_reversal_inverts_every_comparable_pair() -> None:
    score = score_page(annotation(), ("c", "b", "a", "x"))
    assert score.inversion_count == 3
    assert score.pairwise_accuracy.numerator == 0


def test_wrong_set_subset_and_candidate_only_new_wrong_pair() -> None:
    gt = annotation()
    control = score_corpus((gt,), {"H01": ("b", "a", "c", "x")})
    corrected = score_corpus((gt,), {"H01": ("a", "b", "c", "x")})
    bad = score_corpus((gt,), {"H01": ("a", "c", "b", "x")})
    assert wrong_set_comparison(control, corrected)[
        "candidateWrongPairSubsetOfControl"
    ] is True
    comparison = wrong_set_comparison(control, bad)
    assert comparison["candidateWrongPairSubsetOfControl"] is False
    assert comparison["newWrongPairs"] == ["H01:b>c"]


def test_slice_attribution_is_explicit() -> None:
    scores = score_corpus((annotation(),), {"H01": ("b", "a", "c", "x")})
    assert scores.slice("A").wrong_pairs == ("H01:a>b",)
    assert scores.slice("B").wrong_pairs == ()
    assert scores.slice("horizontal-only").comparable_pair_count == 2
