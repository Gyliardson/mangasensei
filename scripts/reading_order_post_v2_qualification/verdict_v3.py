from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import cast

from scripts.reading_order_v3_authoring.contracts import AUTHORING_SLICES

from .contracts import ArmId
from .exercise import ExerciseCount, ExerciseReport
from .exercise_v3 import EXERCISE_MINIMA_V3
from .scoring import (
    CorpusScore,
    candidate_only_wrong_pairs,
    strict_wrong_set_improvement,
    wrong_set_is_subset,
)
from .verdict import (
    ComponentStatus,
    GateReason,
    Verdict,
    VerdictResult,
    _page_map,
    _target_state,
)

V3_REQUIRED_SLICES = frozenset(AUTHORING_SLICES)
_CANONICAL_EXERCISE_MINIMA_V3: Mapping[str, int] = MappingProxyType(
    dict(EXERCISE_MINIMA_V3)
)


def _invalid_result(gate: str, detail: str) -> VerdictResult:
    reason = GateReason(gate, "all", "fail", detail)
    return VerdictResult(
        Verdict.INVALID_EXPERIMENT,
        "harness-invalid",
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        ComponentStatus.NOT_EVALUATED,
        (reason,),
    )


def _exercise_contract_valid(exercise: ExerciseReport) -> bool:
    if type(exercise) is not ExerciseReport:
        return False
    if type(exercise.minima) is not dict or set(exercise.minima) != set(
        _CANONICAL_EXERCISE_MINIMA_V3
    ):
        return False
    if any(
        type(exercise.minima[name]) is not int
        or exercise.minima[name] != canonical_minimum
        for name, canonical_minimum in _CANONICAL_EXERCISE_MINIMA_V3.items()
    ):
        return False
    if type(exercise.counts) is not dict:
        return False
    for name in _CANONICAL_EXERCISE_MINIMA_V3:
        count = exercise.counts.get(name)
        if type(count) is not ExerciseCount:
            return False
        validated_count = cast(ExerciseCount, count)
        count_value = cast(object, validated_count.count)
        if type(count_value) is not int or count_value < 0:
            return False
    return True


def _exercise_minimum_met(exercise: ExerciseReport, name: str) -> bool:
    count = cast(ExerciseCount, exercise.counts[name])
    count_value = cast(int, count.count)
    return count_value >= _CANONICAL_EXERCISE_MINIMA_V3[name]


def _universal_v3(
    baseline: CorpusScore,
    candidate: CorpusScore,
    *,
    arm: str,
) -> tuple[bool, list[GateReason]]:
    reasons: list[GateReason] = []
    if not wrong_set_is_subset(baseline.aggregate, candidate.aggregate):
        reasons.append(
            GateReason(
                "wrong-set-subset",
                arm,
                "fail",
                "new wrong pairs: "
                f"{candidate_only_wrong_pairs(baseline.aggregate, candidate.aggregate)}",
            )
        )
    if candidate.aggregate.pairwise_accuracy < baseline.aggregate.pairwise_accuracy:
        reasons.append(
            GateReason("global-accuracy", arm, "fail", "global pairwise accuracy regressed")
        )
    if candidate.aggregate.normalized_error > baseline.aggregate.normalized_error:
        reasons.append(
            GateReason("global-normalized-error", arm, "fail", "normalized error increased")
        )
    if candidate.exact_sequence_pages < baseline.exact_sequence_pages:
        reasons.append(
            GateReason(
                "exact-sequence-pages",
                arm,
                "fail",
                "exact-sequence page count decreased",
            )
        )

    baseline_pages = _page_map(baseline)
    candidate_pages = _page_map(candidate)
    for page_id in sorted(baseline_pages):
        if not wrong_set_is_subset(baseline_pages[page_id], candidate_pages[page_id]):
            reasons.append(
                GateReason(
                    "page-no-new-inversion",
                    arm,
                    "fail",
                    f"{page_id} gained a new wrong pair",
                )
            )

    for slice_name in sorted(V3_REQUIRED_SLICES):
        base_slice = baseline.slices.get(slice_name)
        candidate_slice = candidate.slices.get(slice_name)
        if base_slice is None or base_slice.comparable_pairs <= 0:
            reasons.append(
                GateReason(
                    "required-slice",
                    arm,
                    "fail",
                    f"baseline slice {slice_name} missing/noncomparable",
                )
            )
            continue
        if candidate_slice is None or candidate_slice.comparable_pairs <= 0:
            reasons.append(
                GateReason(
                    "required-slice",
                    arm,
                    "fail",
                    f"candidate slice {slice_name} missing/noncomparable",
                )
            )
            continue
        if candidate_slice.pairwise_accuracy < base_slice.pairwise_accuracy:
            reasons.append(
                GateReason(
                    "slice-accuracy",
                    arm,
                    "fail",
                    f"{slice_name} accuracy regressed",
                )
            )
        if candidate_slice.normalized_error > base_slice.normalized_error:
            reasons.append(
                GateReason(
                    "slice-normalized-error",
                    arm,
                    "fail",
                    f"{slice_name} normalized error increased",
                )
            )
    return not reasons, reasons


def _component_v3(
    *,
    name: str,
    comparisons: tuple[tuple[CorpusScore, CorpusScore, str], ...],
    exercise_names: tuple[str, ...],
    target_slices: tuple[str, ...],
    exercise: ExerciseReport,
) -> tuple[ComponentStatus, list[GateReason]]:
    reasons: list[GateReason] = []
    safe = True
    for baseline, candidate, arm_name in comparisons:
        comparison_safe, comparison_reasons = _universal_v3(
            baseline, candidate, arm=arm_name
        )
        safe = safe and comparison_safe
        reasons.extend(comparison_reasons)
    if not safe:
        reasons.append(
            GateReason(f"{name}-safety", name, "fail", "attributable arm introduced regression")
        )
        return ComponentStatus.FAIL, reasons

    unmet = [
        exercise_name
        for exercise_name in exercise_names
        if not _exercise_minimum_met(exercise, exercise_name)
    ]
    if unmet:
        reasons.append(
            GateReason(
                f"{name}-exercise",
                name,
                "inconclusive",
                f"mechanism exercise minima not met: {unmet}",
            )
        )
        return ComponentStatus.INCONCLUSIVE, reasons

    target_pairs = tuple((baseline, candidate) for baseline, candidate, _ in comparisons)
    has_failure, improved = _target_state(target_pairs, target_slices)
    if not has_failure:
        reasons.append(
            GateReason(
                f"{name}-target-control-failure",
                name,
                "inconclusive",
                "predeclared target slices contain no baseline wrong pair to improve",
            )
        )
        return ComponentStatus.INCONCLUSIVE, reasons
    if not improved:
        reasons.append(
            GateReason(
                f"{name}-strict-improvement",
                name,
                "fail",
                "predeclared target wrong-pair set did not strictly improve",
            )
        )
        return ComponentStatus.FAIL, reasons
    return ComponentStatus.PASS, reasons


def evaluate_verdict_v3(
    *,
    harness_valid: bool,
    scores: dict[ArmId, CorpusScore],
    exercise: ExerciseReport,
) -> VerdictResult:
    """Evaluate the v3 verdict with generic C3 rejection-page reachability."""

    if harness_valid is not True:
        return _invalid_result("harness-validity", "experiment validity gate failed")
    if not _exercise_contract_valid(exercise):
        return _invalid_result(
            "exercise-policy-validity",
            "v3 exercise report policy or required count shape is invalid",
        )

    reasons: list[GateReason] = []
    control = scores[ArmId.CONTROL]

    c1_status, component_reasons = _component_v3(
        name="C1",
        comparisons=(
            (control, scores[ArmId.C1_ONLY], ArmId.C1_ONLY.value),
            (scores[ArmId.C2_ONLY], scores[ArmId.C1_C2], ArmId.C1_C2.value),
        ),
        exercise_names=("c1_guarded_pairs",),
        target_slices=("c1-boundary-positive",),
        exercise=exercise,
    )
    reasons.extend(component_reasons)

    c2_status, component_reasons = _component_v3(
        name="C2",
        comparisons=(
            (control, scores[ArmId.C2_ONLY], ArmId.C2_ONLY.value),
            (scores[ArmId.C1_ONLY], scores[ArmId.C1_C2], ArmId.C1_C2.value),
        ),
        exercise_names=(
            "c2_gutter_pairs",
            "c2_overlap_pairs",
            "c2_pair_precedence_pairs",
            "c2_fail_closed_no_relation_pairs",
            "c2_conflict_cycle_fallback_pairs",
        ),
        target_slices=(
            "c2-gutter-bridge",
            "c2-ambiguous-overlap-bridge",
            "c2-pair-precedence-slot",
        ),
        exercise=exercise,
    )
    reasons.extend(component_reasons)

    c3_status, component_reasons = _component_v3(
        name="C3",
        comparisons=(
            (control, scores[ArmId.C3_ONLY], ArmId.C3_ONLY.value),
            (scores[ArmId.C1_C2], scores[ArmId.C1_C2_C3], ArmId.C1_C2_C3.value),
        ),
        exercise_names=("c3_positive_pairs", "c3_rejection_pages"),
        target_slices=("c3-positive-recovery",),
        exercise=exercise,
    )
    reasons.extend(component_reasons)

    b1_status, component_reasons = _component_v3(
        name="B1",
        comparisons=(
            (control, scores[ArmId.B1_ONLY], ArmId.B1_ONLY.value),
            (
                scores[ArmId.C1_C2_C3],
                scores[ArmId.C1_C2_C3_B1],
                ArmId.C1_C2_C3_B1.value,
            ),
        ),
        exercise_names=("b1_horizontal_pairs", "b1_vertical_pairs", "b1_mixed_pairs"),
        target_slices=("b1-horizontal", "b1-vertical", "b1-mixed-orientation"),
        exercise=exercise,
    )
    reasons.extend(component_reasons)

    final = scores[ArmId.C1_C2_C3_B1]
    final_safe, final_reasons = _universal_v3(
        control, final, arm=ArmId.C1_C2_C3_B1.value
    )
    reasons.extend(final_reasons)
    if not final_safe:
        final_status = ComponentStatus.FAIL
    elif all(
        status is ComponentStatus.PASS for status in (c1_status, c2_status, c3_status, b1_status)
    ):
        global_improvement = strict_wrong_set_improvement(control.aggregate, final.aggregate)
        control_combined = control.slices["combined-c1-c2-c3-b1"]
        final_combined = final.slices["combined-c1-c2-c3-b1"]
        combined_improvement = strict_wrong_set_improvement(control_combined, final_combined)
        if global_improvement and combined_improvement:
            final_status = ComponentStatus.PASS
        else:
            final_status = ComponentStatus.FAIL
            reasons.append(
                GateReason(
                    "FINAL-strict-improvement",
                    ArmId.C1_C2_C3_B1.value,
                    "fail",
                    "final arm must strictly improve global and combined-target wrong-pair sets",
                )
            )
    else:
        final_status = ComponentStatus.NOT_EVALUATED

    if c1_status is ComponentStatus.FAIL:
        verdict = Verdict.C1_FAIL
    elif c2_status is ComponentStatus.FAIL:
        verdict = Verdict.C2_FAIL
    elif c3_status is ComponentStatus.FAIL:
        verdict = Verdict.C3_FAIL
    elif b1_status is ComponentStatus.FAIL:
        verdict = Verdict.B1_FAIL
    elif final_status is ComponentStatus.FAIL:
        verdict = Verdict.FINAL_FAIL
    elif c1_status is ComponentStatus.INCONCLUSIVE:
        verdict = Verdict.C1_INCONCLUSIVE
    elif c2_status is ComponentStatus.INCONCLUSIVE:
        verdict = Verdict.C2_INCONCLUSIVE
    elif c3_status is ComponentStatus.INCONCLUSIVE:
        verdict = Verdict.C3_INCONCLUSIVE
    elif b1_status is ComponentStatus.INCONCLUSIVE:
        verdict = Verdict.B1_INCONCLUSIVE
    else:
        verdict = Verdict.READING_ORDER_POST_V2_HELDOUT_PASS

    return VerdictResult(
        verdict,
        "VALID",
        c1_status,
        c2_status,
        c3_status,
        b1_status,
        final_status,
        tuple(reasons),
    )
