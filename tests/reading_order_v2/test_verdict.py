from __future__ import annotations

from fractions import Fraction

from scripts.reading_order_v2.contracts import REQUIRED_SLICES
from scripts.reading_order_v2.scoring import CorpusScore, PairMetrics
from scripts.reading_order_v2.verdict import Verdict, evaluate_verdict


def _metric(
    wrong: tuple[tuple[str, str], ...], *, total: int = 4
) -> PairMetrics:
    inversions = len(wrong)
    return PairMetrics(
        total,
        inversions,
        total - inversions,
        Fraction(total - inversions, total),
        Fraction(inversions, total),
        wrong,
    )


def _score(global_wrong=(), a_wrong=(), b_wrong=()) -> CorpusScore:
    slices = {name: _metric(()) for name in REQUIRED_SLICES}
    slices["A"] = _metric(tuple(a_wrong))
    slices["B"] = _metric(tuple(b_wrong))
    return CorpusScore(
        page_count=16,
        exact_sequence_pages=16 - len(global_wrong),
        aggregate=_metric(tuple(global_wrong)),
        slices=slices,
        pages=(),
    )


def _evaluate(
    control: CorpusScore,
    panel_only: CorpusScore,
    order_only: CorpusScore,
    combined: CorpusScore,
    *,
    harness_valid: bool = True,
    a_exercised: bool = True,
    b_exercised: bool = True,
):
    return evaluate_verdict(
        harness_valid=harness_valid,
        control=control,
        panel_only=panel_only,
        order_only=order_only,
        combined=combined,
        a_exercised=a_exercised,
        b_exercised=b_exercised,
    )


def test_invalid_experiment_precedes_quality() -> None:
    score = _score()
    result = _evaluate(score, score, score, score, harness_valid=False)
    assert result.verdict is Verdict.INVALID_EXPERIMENT


def test_a_and_b_inconclusive_are_formal_outcomes() -> None:
    control = _score((("a", "b"),), (("a", "b"),), (("c", "d"),))
    improved = _score()
    result = _evaluate(control, improved, improved, improved, a_exercised=False)
    assert result.verdict is Verdict.A_INCONCLUSIVE
    result = _evaluate(control, improved, improved, improved, b_exercised=False)
    assert result.verdict is Verdict.B_INCONCLUSIVE


def test_a_b_combined_fail_and_pass_paths() -> None:
    control = _score(
        (("a", "b"), ("c", "d")),
        (("a", "b"),),
        (("c", "d"),),
    )
    a_pass = _score((("c", "d"),), (), (("c", "d"),))
    b_pass = _score((("a", "b"),), (("a", "b"),), ())
    combined_pass = _score((), (), ())
    result = _evaluate(control, a_pass, b_pass, combined_pass)
    assert result.verdict is Verdict.READING_ORDER_V2_HELDOUT_PASS

    a_fail = _score((("c", "d"), ("x", "y")), (("x", "y"),), (("c", "d"),))
    assert _evaluate(control, a_fail, b_pass, combined_pass).verdict is Verdict.A_FAIL

    b_fail = _score((("a", "b"), ("x", "y")), (("a", "b"),), (("x", "y"),))
    assert _evaluate(control, a_pass, b_fail, combined_pass).verdict is Verdict.B_FAIL

    combined_fail = _score((("a", "b"),), (("a", "b"),), ())
    result = _evaluate(control, a_pass, b_pass, combined_fail)
    assert result.verdict is Verdict.COMBINED_FAIL


def test_exercised_hypothesis_without_control_failure_is_inclusive() -> None:
    no_a_failure = _score((("c", "d"),), (), (("c", "d"),))
    b_pass = _score((), (), ())
    result = _evaluate(no_a_failure, no_a_failure, b_pass, b_pass)
    assert result.verdict is Verdict.A_INCONCLUSIVE
    assert result.a_status == "INCONCLUSIVE"

    no_b_failure = _score((("a", "b"),), (("a", "b"),), ())
    a_pass = _score((), (), ())
    result = _evaluate(no_b_failure, a_pass, no_b_failure, a_pass)
    assert result.verdict is Verdict.B_INCONCLUSIVE
    assert result.b_status == "INCONCLUSIVE"


def test_candidate_missing_required_control_slice_cannot_pass() -> None:
    control = _score(
        (("a", "b"), ("c", "d")),
        (("a", "b"),),
        (("c", "d"),),
    )
    a_pass = _score((("c", "d"),), (), (("c", "d"),))
    b_pass = _score((("a", "b"),), (("a", "b"),), ())
    combined = _score()
    del a_pass.slices["horizontal-only"]
    result = _evaluate(control, a_pass, b_pass, combined)
    assert result.verdict is Verdict.A_FAIL
    assert any(reason.gate == "required-slice" for reason in result.reasons)
