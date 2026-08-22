from __future__ import annotations

import hashlib
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import pytest
import scripts.reading_order_post_v2_qualification.verdict_v3 as verdict_v3_module
from scripts.reading_order_post_v2_qualification.contracts import (
    EXERCISE_MINIMA,
    REQUIRED_SLICES,
    ArmId,
)
from scripts.reading_order_post_v2_qualification.exercise import ExerciseCount, ExerciseReport
from scripts.reading_order_post_v2_qualification.scoring import CorpusScore, PairMetrics
from scripts.reading_order_post_v2_qualification.verdict import (
    ComponentStatus,
    Verdict,
    VerdictResult,
    evaluate_verdict,
)
from scripts.reading_order_post_v2_qualification.verdict_v3 import (
    evaluate_verdict_v3,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
VERDICT_V2_PATH = (
    REPO_ROOT / "scripts" / "reading_order_post_v2_qualification" / "verdict.py"
)
VERDICT_V2_BLOB = "08c036c37e329039898e7ad3cf5e4d854407e129"
LEGACY_C3_METRICS = {
    "c3_zero_multiple_anchor_rejection_pairs",
    "c3_zero_multiple_companion_rejection_pairs",
    "c3_invalid_topology_rejection_pairs",
    "c3_insufficient_visible_support_rejection_pairs",
}
EXERCISE_MINIMA_V3 = {
    name: minimum
    for name, minimum in EXERCISE_MINIMA.items()
    if name not in LEGACY_C3_METRICS
}
EXERCISE_MINIMA_V3["c3_rejection_pages"] = 8

_WRONG = {
    "C1": ("Q001", "c1-a", "c1-b"),
    "C2": ("Q002", "c2-a", "c2-b"),
    "C3": ("Q003", "c3-a", "c3-b"),
    "B1": ("Q004", "b1-a", "b1-b"),
}
_TARGETS = {
    "c1-boundary-positive": "C1",
    "c2-gutter-bridge": "C2",
    "c2-ambiguous-overlap-bridge": "C2",
    "c2-pair-precedence-slot": "C2",
    "c3-positive-recovery": "C3",
    "b1-horizontal": "B1",
    "b1-vertical": "B1",
    "b1-mixed-orientation": "B1",
    "combined-c1-c2-c3-b1": "ALL",
}
_ENABLED = {
    ArmId.CONTROL: frozenset(),
    ArmId.C1_ONLY: frozenset({"C1"}),
    ArmId.C2_ONLY: frozenset({"C2"}),
    ArmId.C1_C2: frozenset({"C1", "C2"}),
    ArmId.C3_ONLY: frozenset({"C3"}),
    ArmId.C1_C2_C3: frozenset({"C1", "C2", "C3"}),
    ArmId.B1_ONLY: frozenset({"B1"}),
    ArmId.C1_C2_C3_B1: frozenset({"C1", "C2", "C3", "B1"}),
}


def _metric(wrong: tuple[tuple[str, str, str], ...]) -> PairMetrics:
    total = 8
    wrong_count = len(wrong)
    return PairMetrics(
        comparable_pairs=total,
        correct_pairs=total - wrong_count,
        wrong_pairs_count=wrong_count,
        pairwise_accuracy=Fraction(total - wrong_count, total),
        normalized_inversion_distance=Fraction(wrong_count, total),
        normalized_error=Fraction(wrong_count, total),
        wrong_pairs=wrong,
    )


def _score(arm: ArmId) -> CorpusScore:
    enabled = _ENABLED[arm]
    aggregate_wrong = tuple(_WRONG[name] for name in _WRONG if name not in enabled)
    slices: dict[str, PairMetrics] = {}
    for name in REQUIRED_SLICES:
        target = _TARGETS.get(name)
        if target == "ALL":
            wrong = aggregate_wrong
        elif target is not None and target not in enabled:
            wrong = (_WRONG[target],)
        else:
            wrong = ()
        slices[name] = _metric(wrong)
    return CorpusScore(
        page_count=8,
        exact_sequence_pages=8 - len(aggregate_wrong),
        aggregate=_metric(aggregate_wrong),
        slices=slices,
        pages=(),
    )


def _scores() -> dict[ArmId, CorpusScore]:
    return {arm: _score(arm) for arm in ArmId}


def _report(
    minima: dict[str, int],
    *,
    overrides: dict[str, int] | None = None,
    extras: dict[str, int] | None = None,
) -> ExerciseReport:
    values = {**minima, **(overrides or {}), **(extras or {})}
    return ExerciseReport(
        counts={name: ExerciseCount(value, (), ()) for name, value in values.items()},
        minima=dict(minima),
    )


def _v2_report() -> ExerciseReport:
    return _report(EXERCISE_MINIMA)


def _v3_report(
    *,
    positive: int = 4,
    rejection: int = 8,
    extras: dict[str, int] | None = None,
) -> ExerciseReport:
    return _report(
        EXERCISE_MINIMA_V3,
        overrides={"c3_positive_pairs": positive, "c3_rejection_pages": rejection},
        extras=extras,
    )


def _evaluate_v3(
    scores: dict[ArmId, CorpusScore] | None = None,
    exercise: ExerciseReport | None = None,
) -> VerdictResult:
    return evaluate_verdict_v3(
        harness_valid=True,
        scores=scores or _scores(),
        exercise=exercise or _v3_report(),
    )


def _replace_score(
    scores: dict[ArmId, CorpusScore], arm: ArmId, score: CorpusScore
) -> dict[ArmId, CorpusScore]:
    return {**scores, arm: score}


def _with_slice(score: CorpusScore, name: str, metric: PairMetrics) -> CorpusScore:
    return CorpusScore(
        page_count=score.page_count,
        exact_sequence_pages=score.exact_sequence_pages,
        aggregate=score.aggregate,
        slices={**score.slices, name: metric},
        pages=score.pages,
    )


def _with_aggregate(score: CorpusScore, metric: PairMetrics) -> CorpusScore:
    return CorpusScore(
        page_count=score.page_count,
        exact_sequence_pages=score.exact_sequence_pages,
        aggregate=metric,
        slices=score.slices,
        pages=score.pages,
    )


def _git_blob_sha(path: Path) -> str:
    content = path.read_bytes()
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()  # noqa: S324


def test_v3_c3_binding_is_exactly_positive_pairs_and_rejection_pages() -> None:
    scores = _scores()
    with patch.object(
        verdict_v3_module,
        "_component",
        wraps=verdict_v3_module._component,
    ) as component:
        _evaluate_v3(scores=scores)
    c3_call = next(call for call in component.call_args_list if call.kwargs["name"] == "C3")
    comparisons = c3_call.kwargs["comparisons"]
    assert c3_call.kwargs["exercise_names"] == (
        "c3_positive_pairs",
        "c3_rejection_pages",
    )
    assert c3_call.kwargs["target_slices"] == ("c3-positive-recovery",)
    assert comparisons == (
        (scores[ArmId.CONTROL], scores[ArmId.C3_ONLY], ArmId.C3_ONLY.value),
        (scores[ArmId.C1_C2], scores[ArmId.C1_C2_C3], ArmId.C1_C2_C3.value),
    )


@pytest.mark.parametrize(
    ("positive", "rejection"),
    [(3, 8), (4, 7)],
)
def test_v3_c3_is_inconclusive_below_either_exercise_minimum(
    positive: int, rejection: int
) -> None:
    result = _evaluate_v3(exercise=_v3_report(positive=positive, rejection=rejection))
    assert result.verdict is Verdict.C3_INCONCLUSIVE
    assert result.c3_status is ComponentStatus.INCONCLUSIVE
    assert any(reason.gate == "C3-exercise" for reason in result.reasons)


def test_v3_c3_exercise_prerequisite_is_satisfied_at_both_minima() -> None:
    result = _evaluate_v3(exercise=_v3_report(positive=4, rejection=8))
    assert result.c3_status is ComponentStatus.PASS
    assert result.final_status is ComponentStatus.PASS
    assert result.verdict is Verdict.READING_ORDER_POST_V2_HELDOUT_PASS
    assert result.reasons == ()
    assert not any(reason.gate == "C3-exercise" for reason in result.reasons)


def test_v3_exercise_report_needs_no_legacy_c3_metric_names() -> None:
    report = _v3_report()
    assert LEGACY_C3_METRICS.isdisjoint(report.counts)
    assert _evaluate_v3(exercise=report).c3_status is ComponentStatus.PASS


def test_legacy_c3_values_cannot_satisfy_or_override_rejection_pages() -> None:
    result = _evaluate_v3(
        exercise=_v3_report(
            rejection=7,
            extras={name: 100 for name in LEGACY_C3_METRICS},
        )
    )
    assert result.c3_status is ComponentStatus.INCONCLUSIVE


@pytest.mark.parametrize("arm", [ArmId.C3_ONLY, ArmId.C1_C2_C3])
def test_c3_safety_behavior_remains_equivalent_to_v2(arm: ArmId) -> None:
    scores = _scores()
    regressed = _metric((*scores[ArmId.CONTROL].aggregate.wrong_pairs, ("Q999", "x", "y")))
    scores = _replace_score(
        scores,
        arm,
        _with_aggregate(scores[arm], regressed),
    )
    v2 = evaluate_verdict(harness_valid=True, scores=scores, exercise=_v2_report())
    v3 = _evaluate_v3(scores=scores)
    assert (v3.c3_status, v3.verdict, v3.reasons) == (
        v2.c3_status,
        v2.verdict,
        v2.reasons,
    )
    assert v3.verdict is Verdict.C3_FAIL


def test_c3_target_control_failure_behavior_remains_equivalent_to_v2() -> None:
    scores = _scores()
    for arm in ArmId:
        scores = _replace_score(
            scores,
            arm,
            _with_slice(scores[arm], "c3-positive-recovery", _metric(())),
        )
    v2 = evaluate_verdict(harness_valid=True, scores=scores, exercise=_v2_report())
    v3 = _evaluate_v3(scores=scores)
    assert (v3.c3_status, v3.verdict, v3.reasons) == (
        v2.c3_status,
        v2.verdict,
        v2.reasons,
    )
    assert v3.verdict is Verdict.C3_INCONCLUSIVE
    assert any(reason.gate == "C3-target-control-failure" for reason in v3.reasons)


def test_c3_strict_improvement_behavior_remains_equivalent_to_v2() -> None:
    scores = _scores()
    unchanged = scores[ArmId.CONTROL].slices["c3-positive-recovery"]
    scores = _replace_score(
        scores,
        ArmId.C3_ONLY,
        _with_slice(scores[ArmId.C3_ONLY], "c3-positive-recovery", unchanged),
    )
    scores = _replace_score(
        scores,
        ArmId.C1_C2_C3,
        _with_slice(scores[ArmId.C1_C2_C3], "c3-positive-recovery", unchanged),
    )
    v2 = evaluate_verdict(harness_valid=True, scores=scores, exercise=_v2_report())
    v3 = _evaluate_v3(scores=scores)
    assert (v3.c3_status, v3.verdict, v3.reasons) == (
        v2.c3_status,
        v2.verdict,
        v2.reasons,
    )
    assert any(reason.gate == "C3-strict-improvement" for reason in v3.reasons)


@pytest.mark.parametrize(
    ("component", "exercise_name"),
    [
        ("c1", "c1_guarded_pairs"),
        ("c2", "c2_gutter_pairs"),
        ("b1", "b1_horizontal_pairs"),
    ],
)
def test_non_c3_components_are_differentially_equivalent_to_v2(
    component: str, exercise_name: str
) -> None:
    v2_report = _report(EXERCISE_MINIMA, overrides={exercise_name: 0})
    v3_report = _report(EXERCISE_MINIMA_V3, overrides={exercise_name: 0})
    v2 = evaluate_verdict(harness_valid=True, scores=_scores(), exercise=v2_report)
    v3 = _evaluate_v3(exercise=v3_report)
    assert getattr(v3, f"{component}_status") is getattr(v2, f"{component}_status")
    assert v3.reasons == v2.reasons


@pytest.mark.parametrize(
    ("component", "arm", "expected_verdict"),
    [
        ("c1", ArmId.C1_ONLY, Verdict.C1_FAIL),
        ("c2", ArmId.C2_ONLY, Verdict.C2_FAIL),
        ("b1", ArmId.B1_ONLY, Verdict.B1_FAIL),
    ],
)
def test_non_c3_failure_outcomes_are_differentially_equivalent_to_v2(
    component: str,
    arm: ArmId,
    expected_verdict: Verdict,
) -> None:
    scores = _scores()
    regressed = _metric((*scores[ArmId.CONTROL].aggregate.wrong_pairs, ("Q999", "x", "y")))
    scores = _replace_score(scores, arm, _with_aggregate(scores[arm], regressed))
    v2 = evaluate_verdict(harness_valid=True, scores=scores, exercise=_v2_report())
    v3 = _evaluate_v3(scores=scores)
    assert getattr(v3, f"{component}_status") is ComponentStatus.FAIL
    assert (v3.verdict, v3.reasons) == (v2.verdict, v2.reasons)
    assert v3.verdict is expected_verdict


@pytest.mark.parametrize("gate", ["global", "combined"])
def test_final_strict_improvement_gates_are_differentially_equivalent_to_v2(
    gate: str,
) -> None:
    scores = _scores()
    if gate == "global":
        control_metric = scores[ArmId.CONTROL].aggregate
        scores = {
            arm: _with_aggregate(score, control_metric) for arm, score in scores.items()
        }
    else:
        control_metric = scores[ArmId.CONTROL].slices["combined-c1-c2-c3-b1"]
        scores = {
            arm: _with_slice(score, "combined-c1-c2-c3-b1", control_metric)
            for arm, score in scores.items()
        }
    v2 = evaluate_verdict(harness_valid=True, scores=scores, exercise=_v2_report())
    v3 = _evaluate_v3(scores=scores)
    assert (v3.final_status, v3.verdict, v3.reasons) == (
        v2.final_status,
        v2.verdict,
        v2.reasons,
    )
    assert v3.verdict is Verdict.FINAL_FAIL


def test_final_universal_safety_remains_differentially_equivalent_to_v2() -> None:
    scores = _scores()
    final = scores[ArmId.C1_C2_C3_B1]
    regressed = _metric((*scores[ArmId.CONTROL].aggregate.wrong_pairs, ("Q999", "x", "y")))
    scores = _replace_score(scores, ArmId.C1_C2_C3_B1, _with_aggregate(final, regressed))
    v2 = evaluate_verdict(harness_valid=True, scores=scores, exercise=_v2_report())
    v3 = _evaluate_v3(scores=scores)
    assert (v3.final_status, v3.verdict, v3.reasons) == (
        v2.final_status,
        v2.verdict,
        v2.reasons,
    )
    assert v3.final_status is ComponentStatus.FAIL
    assert any(reason.arm == ArmId.C1_C2_C3_B1.value for reason in v3.reasons)


@pytest.mark.parametrize("harness_valid", [False, None, 0, "false"])
def test_invalid_v3_boundary_short_circuits_before_evidence_evaluation(
    harness_valid: object,
) -> None:
    with (
        patch.object(verdict_v3_module, "_component") as component,
        patch.object(verdict_v3_module, "_universal") as universal,
    ):
        result = evaluate_verdict_v3(
            harness_valid=harness_valid,  # type: ignore[arg-type]
            scores={},
            exercise=_report({}, extras={}),
        )
    component.assert_not_called()
    universal.assert_not_called()
    assert result.verdict is Verdict.INVALID_EXPERIMENT
    assert result.harness_status == "harness-invalid"
    assert (
        result.c1_status,
        result.c2_status,
        result.c3_status,
        result.b1_status,
        result.final_status,
    ) == (ComponentStatus.NOT_EVALUATED,) * 5
    assert result.reasons[0].gate == "harness-validity"


def test_v2_verdict_remains_the_expected_historical_blob() -> None:
    assert _git_blob_sha(VERDICT_V2_PATH) == VERDICT_V2_BLOB
