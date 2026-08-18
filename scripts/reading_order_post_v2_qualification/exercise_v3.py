from __future__ import annotations

from .contracts import ArmId, PageGroundTruth
from .exercise import ExerciseCount, ExerciseReport, build_exercise_report

C3_NEGATIVE_SLICES = frozenset(
    {
        "c3-zero-multiple-anchor-negative",
        "c3-zero-multiple-companion-negative",
        "c3-invalid-topology-negative",
        "c3-insufficient-visible-support-negative",
    }
)

EXERCISE_MINIMA_V3 = {
    "c1_guarded_pairs": 4,
    "c2_gutter_pairs": 3,
    "c2_overlap_pairs": 3,
    "c2_pair_precedence_pairs": 3,
    "c2_fail_closed_no_relation_pairs": 2,
    "c2_conflict_cycle_fallback_pairs": 2,
    "c3_positive_pairs": 4,
    "c3_rejection_pairs": 8,
    "b1_horizontal_pairs": 4,
    "b1_vertical_pairs": 4,
    "b1_mixed_pairs": 4,
}


def _count(records: set[tuple[str, str]]) -> ExerciseCount:
    pair_ids = tuple(sorted(f"{page_id}:{pair_id}" for page_id, pair_id in records))
    page_ids = tuple(sorted({page_id for page_id, _ in records}))
    return ExerciseCount(len(records), pair_ids, page_ids)


def _c3_generic_rejects(diagnostic: dict[str, object]) -> bool:
    pre = diagnostic.get("preSegmentation")
    reason = diagnostic.get("recoveryReason")
    return (
        isinstance(pre, dict)
        and pre.get("reason") == "fewer-than-two-groups"
        and pre.get("boxCount") == 1
        and isinstance(reason, str)
        and reason.startswith("rejected-")
        and diagnostic.get("assignments") == []
        and diagnostic.get("relationEdges") == []
        and diagnostic.get("finalOrder") == diagnostic.get("fallbackOrder")
        and diagnostic.get("usedPanelEvidence") is False
    )


def _c3_rejection_records(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    arms = (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    for page in annotations:
        for pair in page.qualification_pairs:
            if not C3_NEGATIVE_SLICES.intersection(pair.slices):
                continue
            if any(_c3_generic_rejects(diagnostics[arm][page.page_id]) for arm in arms):
                records.add((page.page_id, pair.pair_id))
    return records


def build_exercise_report_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> ExerciseReport:
    """Build the v3 reachability report without mutating the consumed v2 evaluator.

    Positive C1/C2/C3 and B1 predicates remain byte-for-byte delegated to the
    frozen v2 evaluator. The four v2 category-named C3 rejection counters are
    intentionally replaced by one generic rejection metric because the frozen
    candidate diagnostic vocabulary cannot distinguish those four causal
    categories at qualification time.
    """

    v2 = build_exercise_report(annotations=annotations, diagnostics=diagnostics)
    counts = {
        name: v2.counts[name]
        for name in EXERCISE_MINIMA_V3
        if name != "c3_rejection_pairs"
    }
    counts["c3_rejection_pairs"] = _count(_c3_rejection_records(annotations, diagnostics))
    return ExerciseReport(counts=counts, minima=dict(EXERCISE_MINIMA_V3))


def exercise_minimum_met_v3(report: ExerciseReport, name: str) -> bool:
    return report.counts[name].count >= report.minima[name]
