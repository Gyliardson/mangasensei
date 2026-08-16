from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import REQUIRED_SLICES, ArmId
from .exercise import ExerciseReport, exercise_minimum_met
from .scoring import (
    CorpusScore,
    PairMetrics,
    candidate_only_wrong_pairs,
    strict_wrong_set_improvement,
    wrong_set_is_subset,
)


class ComponentStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 -- Formal status vocabulary, not a credential.
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_EVALUATED = "NOT_EVALUATED"


class Verdict(StrEnum):
    READING_ORDER_POST_V2_HELDOUT_PASS = "READING_ORDER_POST_V2_HELDOUT_PASS"  # noqa: S105
    INVALID_EXPERIMENT = "INVALID_EXPERIMENT"
    C1_FAIL = "C1_FAIL"
    C2_FAIL = "C2_FAIL"
    C3_FAIL = "C3_FAIL"
    B1_FAIL = "B1_FAIL"
    FINAL_FAIL = "FINAL_FAIL"
    C1_INCONCLUSIVE = "C1_INCONCLUSIVE"
    C2_INCONCLUSIVE = "C2_INCONCLUSIVE"
    C3_INCONCLUSIVE = "C3_INCONCLUSIVE"
    B1_INCONCLUSIVE = "B1_INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class GateReason:
    gate: str
    arm: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class VerdictResult:
    verdict: Verdict
    harness_status: str
    c1_status: ComponentStatus
    c2_status: ComponentStatus
    c3_status: ComponentStatus
    b1_status: ComponentStatus
    final_status: ComponentStatus
    reasons: tuple[GateReason, ...]


def _page_map(score: CorpusScore) -> dict[str, PairMetrics]:
    return {page.page_id: page.aggregate for page in score.pages}


def _universal(
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

    for slice_name in sorted(REQUIRED_SLICES):
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


def _target_state(
    comparisons: tuple[tuple[CorpusScore, CorpusScore], ...],
    target_slices: tuple[str, ...],
) -> tuple[bool, bool]:
    has_baseline_failure = False
    improved = False
    for baseline, candidate in comparisons:
        for slice_name in target_slices:
            base_slice = baseline.slices.get(slice_name)
            candidate_slice = candidate.slices.get(slice_name)
            if base_slice is None or candidate_slice is None:
                continue
            if base_slice.wrong_pairs:
                has_baseline_failure = True
                if strict_wrong_set_improvement(base_slice, candidate_slice):
                    improved = True
    return has_baseline_failure, improved


def _component(
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
        comparison_safe, comparison_reasons = _universal(baseline, candidate, arm=arm_name)
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
        if not exercise_minimum_met(exercise, exercise_name)
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


def evaluate_verdict(
    *,
    harness_valid: bool,
    scores: dict[ArmId, CorpusScore],
    exercise: ExerciseReport,
) -> VerdictResult:
    if not harness_valid:
        reason = GateReason("harness-validity", "all", "fail", "experiment validity gate failed")
        return VerdictResult(
            Verdict.INVALID_EXPERIMENT,
            "INVALID",
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
        exercise_names=(
            "c3_positive_pairs",
            "c3_zero_multiple_anchor_rejection_pairs",
            "c3_zero_multiple_companion_rejection_pairs",
            "c3_invalid_topology_rejection_pairs",
            "c3_insufficient_visible_support_rejection_pairs",
        ),
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
