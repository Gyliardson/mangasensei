from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contracts import REQUIRED_SLICES
from .scoring import CorpusScore, PairMetrics, candidate_only_wrong_pairs, wrong_set_is_subset


class Verdict(StrEnum):
    READING_ORDER_V2_HELDOUT_PASS = "READING_ORDER_V2_HELDOUT_PASS"  # noqa: S105
    INVALID_EXPERIMENT = "INVALID_EXPERIMENT"
    A_FAIL = "A_FAIL"
    B_FAIL = "B_FAIL"
    COMBINED_FAIL = "COMBINED_FAIL"
    A_INCONCLUSIVE = "A_INCONCLUSIVE"
    B_INCONCLUSIVE = "B_INCONCLUSIVE"


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
    a_status: str
    b_status: str
    combined_status: str
    reasons: tuple[GateReason, ...]


def _slice(score: CorpusScore, name: str) -> PairMetrics | None:
    return score.slices.get(name)


def _required_slice_nonregression(
    control: CorpusScore, candidate: CorpusScore, arm: str
) -> list[GateReason]:
    reasons: list[GateReason] = []
    for name in sorted(REQUIRED_SLICES):
        control_slice = control.slices.get(name)
        candidate_slice = candidate.slices.get(name)
        if control_slice is None or control_slice.comparable_pairs <= 0:
            reasons.append(
                GateReason(
                    "required-slice",
                    arm,
                    "fail",
                    f"control slice {name} is missing or has no comparable pairs",
                )
            )
            continue
        if candidate_slice is None or candidate_slice.comparable_pairs <= 0:
            reasons.append(
                GateReason(
                    "required-slice",
                    arm,
                    "fail",
                    f"candidate slice {name} is missing or has no comparable pairs",
                )
            )
            continue
        if candidate_slice.pairwise_accuracy < control_slice.pairwise_accuracy:
            reasons.append(
                GateReason(
                    "slice-pairwise-nonregression",
                    arm,
                    "fail",
                    f"slice {name} regressed",
                )
            )
        if (
            candidate_slice.normalized_inversion_distance
            > control_slice.normalized_inversion_distance
        ):
            reasons.append(
                GateReason(
                    "slice-inversion-nonregression",
                    arm,
                    "fail",
                    f"slice {name} regressed",
                )
            )
    return reasons


def _universal(
    control: CorpusScore, candidate: CorpusScore, arm: str
) -> tuple[bool, list[GateReason]]:
    reasons: list[GateReason] = []
    if not wrong_set_is_subset(control.aggregate, candidate.aggregate):
        new = candidate_only_wrong_pairs(control.aggregate, candidate.aggregate)
        reasons.append(
            GateReason("wrong-set-subset", arm, "fail", f"new wrong pairs: {new}")
        )
    if candidate.aggregate.pairwise_accuracy < control.aggregate.pairwise_accuracy:
        reasons.append(
            GateReason(
                "aggregate-pairwise-nonregression",
                arm,
                "fail",
                "pairwise accuracy regressed",
            )
        )
    if (
        candidate.aggregate.normalized_inversion_distance
        > control.aggregate.normalized_inversion_distance
    ):
        reasons.append(
            GateReason(
                "aggregate-inversion-nonregression",
                arm,
                "fail",
                "normalized inversion distance increased",
            )
        )
    if candidate.exact_sequence_pages < control.exact_sequence_pages:
        reasons.append(
            GateReason(
                "exact-sequence-pages",
                arm,
                "fail",
                "exact-sequence page count decreased",
            )
        )
    reasons.extend(_required_slice_nonregression(control, candidate, arm))
    return not reasons, reasons


def evaluate_verdict(
    *,
    harness_valid: bool,
    control: CorpusScore,
    panel_only: CorpusScore,
    order_only: CorpusScore,
    combined: CorpusScore,
    a_exercised: bool,
    b_exercised: bool,
) -> VerdictResult:
    """Low-level frozen gate evaluator; normal qualification derives its booleans."""
    reasons: list[GateReason] = []
    if not harness_valid:
        reasons.append(
            GateReason(
                "harness-validity", "all", "fail", "harness validity gate failed"
            )
        )
        return VerdictResult(
            Verdict.INVALID_EXPERIMENT,
            "INVALID",
            "NOT_EVALUATED",
            "NOT_EVALUATED",
            "NOT_EVALUATED",
            tuple(reasons),
        )

    a_universal, a_reasons = _universal(control, panel_only, "A1_B0_PANEL_ONLY")
    reasons.extend(a_reasons)
    if not a_exercised:
        a_status = "INCONCLUSIVE"
        reasons.append(
            GateReason(
                "A-exercise",
                "A1_B0_PANEL_ONLY",
                "inconclusive",
                "partial-assignment mechanism not exercised",
            )
        )
    else:
        control_a = _slice(control, "A")
        candidate_a = _slice(panel_only, "A")
        if control_a is None or candidate_a is None:
            a_status = "FAIL"
            reasons.append(
                GateReason(
                    "A-slice", "A1_B0_PANEL_ONLY", "fail", "required A slice missing"
                )
            )
        elif not control_a.wrong_pairs:
            a_status = "INCONCLUSIVE"
            reasons.append(
                GateReason(
                    "A-control-failure",
                    "A1_B0_PANEL_ONLY",
                    "inconclusive",
                    "control has no A-slice wrong pair to improve",
                )
            )
        elif not a_universal or not set(candidate_a.wrong_pairs) < set(
            control_a.wrong_pairs
        ):
            a_status = "FAIL"
            reasons.append(
                GateReason(
                    "A-strict-improvement",
                    "A1_B0_PANEL_ONLY",
                    "fail",
                    "A wrong-pair set is not a strict subset",
                )
            )
        else:
            a_status = "PASS"

    b_universal, b_reasons = _universal(control, order_only, "A0_B1_ORDER_ONLY")
    reasons.extend(b_reasons)
    if not b_exercised:
        b_status = "INCONCLUSIVE"
        reasons.append(
            GateReason(
                "B-exercise",
                "A0_B1_ORDER_ONLY",
                "inconclusive",
                "orientation-aware mechanism not exercised",
            )
        )
    else:
        control_b = _slice(control, "B")
        candidate_b = _slice(order_only, "B")
        if control_b is None or candidate_b is None:
            b_status = "FAIL"
            reasons.append(
                GateReason(
                    "B-slice", "A0_B1_ORDER_ONLY", "fail", "required B slice missing"
                )
            )
        elif not control_b.wrong_pairs:
            b_status = "INCONCLUSIVE"
            reasons.append(
                GateReason(
                    "B-control-failure",
                    "A0_B1_ORDER_ONLY",
                    "inconclusive",
                    "control has no B-slice wrong pair to improve",
                )
            )
        elif not b_universal or not set(candidate_b.wrong_pairs) < set(
            control_b.wrong_pairs
        ):
            b_status = "FAIL"
            reasons.append(
                GateReason(
                    "B-strict-improvement",
                    "A0_B1_ORDER_ONLY",
                    "fail",
                    "B wrong-pair set is not a strict subset",
                )
            )
        else:
            b_status = "PASS"

    combined_universal, combined_reasons = _universal(
        control, combined, "A1_B1_COMBINED"
    )
    reasons.extend(combined_reasons)
    combined_status = "NOT_EVALUATED"
    if a_status == "PASS" and b_status == "PASS":
        control_wrong = set(control.aggregate.wrong_pairs)
        combined_wrong = set(combined.aggregate.wrong_pairs)
        combined_status = "PASS"
        if not combined_universal or not combined_wrong < control_wrong:
            combined_status = "FAIL"
            reasons.append(
                GateReason(
                    "combined-strict-improvement",
                    "A1_B1_COMBINED",
                    "fail",
                    "combined global wrong set is not a strict subset",
                )
            )
        for slice_name, separate in (("A", panel_only), ("B", order_only)):
            combined_slice = _slice(combined, slice_name)
            separate_slice = _slice(separate, slice_name)
            lost_correction = (
                combined_slice is None
                or separate_slice is None
                or bool(set(combined_slice.wrong_pairs) - set(separate_slice.wrong_pairs))
            )
            if lost_correction:
                combined_status = "FAIL"
                reasons.append(
                    GateReason(
                        "combined-preserves-independent-fix",
                        "A1_B1_COMBINED",
                        "fail",
                        f"combined lost {slice_name} correction",
                    )
                )

    if a_status == "INCONCLUSIVE":
        verdict = Verdict.A_INCONCLUSIVE
    elif a_status != "PASS":
        verdict = Verdict.A_FAIL
    elif b_status == "INCONCLUSIVE":
        verdict = Verdict.B_INCONCLUSIVE
    elif b_status != "PASS":
        verdict = Verdict.B_FAIL
    elif combined_status != "PASS":
        verdict = Verdict.COMBINED_FAIL
    else:
        verdict = Verdict.READING_ORDER_V2_HELDOUT_PASS
    return VerdictResult(
        verdict,
        "VALID",
        a_status,
        b_status,
        combined_status,
        tuple(reasons),
    )
