from __future__ import annotations

from .contracts import ArmId
from .exercise import ExerciseReport
from .scoring import CorpusScore, strict_wrong_set_improvement
from .verdict import (
    ComponentStatus,
    GateReason,
    Verdict,
    VerdictResult,
    _component,
    _universal,
)


def evaluate_verdict_v3(
    *,
    harness_valid: bool,
    scores: dict[ArmId, CorpusScore],
    exercise: ExerciseReport,
) -> VerdictResult:
    """Evaluate the v3 verdict with generic C3 rejection-page reachability."""

    if not harness_valid:
        reason = GateReason("harness-validity", "all", "fail", "experiment validity gate failed")
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

    reasons: list[GateReason] = []
    control = scores[ArmId.CONTROL]

    c1_status, component_reasons = _component(
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

    c2_status, component_reasons = _component(
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

    c3_status, component_reasons = _component(
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

    b1_status, component_reasons = _component(
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
    final_safe, final_reasons = _universal(control, final, arm=ArmId.C1_C2_C3_B1.value)
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
