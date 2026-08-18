from __future__ import annotations

from collections.abc import Iterable
from typing import TypeGuard

from . import DIAGNOSTIC_SCHEMA_VERSION
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
    "c3_rejection_pages": 8,
    "b1_horizontal_pairs": 4,
    "b1_vertical_pairs": 4,
    "b1_mixed_pairs": 4,
}

V3_INVALID_EXPERIMENT_CLASSIFICATION = "INVALID_EXPERIMENT"
V3_INVALID_DIAGNOSTIC_HARNESS_STATUS = "harness-invalid"

_SLICE_ARMS: dict[str, frozenset[ArmId]] = {
    "c1-boundary-positive": frozenset({ArmId.CONTROL, ArmId.C1_ONLY}),
    "c2-gutter-bridge": frozenset(
        {ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1}
    ),
    "c2-ambiguous-overlap-bridge": frozenset(
        {ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1}
    ),
    "c2-pair-precedence-slot": frozenset(
        {ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1}
    ),
    "c2-one-sided-non-unique-fail-closed": frozenset({ArmId.C2_ONLY}),
    "c2-conflict-cycle-safety": frozenset({ArmId.CONTROL, ArmId.C2_ONLY}),
    "c3-positive-recovery": frozenset(
        {ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1}
    ),
    "b1-horizontal": frozenset({ArmId.B1_ONLY, ArmId.C1_C2_C3_B1}),
    "b1-vertical": frozenset({ArmId.B1_ONLY, ArmId.C1_C2_C3_B1}),
    "b1-mixed-orientation": frozenset({ArmId.B1_ONLY, ArmId.C1_C2_C3_B1}),
}
for _slice in C3_NEGATIVE_SLICES:
    _SLICE_ARMS[_slice] = frozenset(
        {ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1}
    )


class V3DiagnosticValidationError(ValueError):
    """Classified v3 diagnostic boundary failure.

    A future executable v3 harness must translate this validation failure to
    INVALID_EXPERIMENT / harness-invalid rather than treating malformed output as
    an unexercised predicate or allowing an accidental evaluator exception.
    """

    classification = V3_INVALID_EXPERIMENT_CLASSIFICATION
    harness_status = V3_INVALID_DIAGNOSTIC_HARNESS_STATUS

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(self.problems))


def _required_arms_by_page(
    annotations: tuple[PageGroundTruth, ...],
) -> dict[str, frozenset[ArmId]]:
    required: dict[str, set[ArmId]] = {}
    for page in annotations:
        page_arms = required.setdefault(page.page_id, set())
        for pair in page.qualification_pairs:
            for slice_name in pair.slices:
                page_arms.update(_SLICE_ARMS.get(slice_name, frozenset()))
    return {page_id: frozenset(arms) for page_id, arms in required.items() if arms}


def _is_string_list(value: object) -> TypeGuard[list[str]]:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_assignment(item: object, where: str, problems: list[str]) -> str | None:
    if not isinstance(item, dict):
        problems.append(f"{where}: assignment must be an object")
        return None
    region_id = item.get("regionId")
    candidate_indices = item.get("candidateGroupIndices")
    status = item.get("status")
    assigned = item.get("assignedGroupIndex")
    uncertain = item.get("uncertainNodeLabel")
    if not isinstance(region_id, str) or not region_id:
        problems.append(f"{where}.regionId: nonempty string required")
        region_id = None
    if not isinstance(candidate_indices, list) or not all(
        type(index) is int and index >= 0 for index in candidate_indices
    ):
        problems.append(f"{where}.candidateGroupIndices: integer array required")
    if status not in {"confident", "unassigned", "ambiguous"}:
        problems.append(f"{where}.status: unsupported assignment status")
    if assigned is not None and (type(assigned) is not int or assigned < 0):
        problems.append(f"{where}.assignedGroupIndex: nonnegative integer or null required")
    if uncertain is not None and not isinstance(uncertain, str):
        problems.append(f"{where}.uncertainNodeLabel: string or null required")
    if (assigned is None) != isinstance(uncertain, str):
        problems.append(
            f"{where}: assignedGroupIndex/uncertainNodeLabel serializer invariant violated"
        )
    return region_id if isinstance(region_id, str) else None


def _validate_relation_edge(item: object, where: str, problems: list[str]) -> None:
    if not isinstance(item, dict):
        problems.append(f"{where}: relation edge must be an object")
        return
    for field in ("sourceNode", "targetNode", "rule"):
        if not isinstance(item.get(field), str) or not item.get(field):
            problems.append(f"{where}.{field}: nonempty string required")


def _validate_diagnostic(
    *,
    diagnostic: object,
    arm: ArmId,
    page: PageGroundTruth,
    problems: list[str],
) -> str | None:
    where = f"diagnostics[{arm.value}][{page.page_id}]"
    if not isinstance(diagnostic, dict):
        problems.append(f"{where}: diagnostic object required")
        return None

    required_fields = {
        "schemaVersion",
        "experimentArm",
        "executionSha",
        "pageId",
        "preSegmentation",
        "segmentation",
        "recoveryReason",
        "assignments",
        "relationEdges",
        "fallbackReason",
        "usedPanelEvidence",
        "fallbackOrder",
        "finalOrder",
        "regionDirections",
    }
    missing = sorted(required_fields - set(diagnostic))
    if missing:
        problems.append(f"{where}: missing required fields {missing}")
        return None

    if diagnostic["schemaVersion"] != DIAGNOSTIC_SCHEMA_VERSION:
        problems.append(f"{where}.schemaVersion: wrong diagnostic schema")
    if diagnostic["experimentArm"] != arm.value:
        problems.append(f"{where}.experimentArm: does not match arm key")
    if diagnostic["pageId"] != page.page_id:
        problems.append(f"{where}.pageId: does not match annotation page")
    execution_sha = diagnostic["executionSha"]
    if not isinstance(execution_sha, str) or len(execution_sha) != 40 or any(
        char not in "0123456789abcdef" for char in execution_sha
    ):
        problems.append(f"{where}.executionSha: lowercase 40-hex SHA required")
        execution_sha = None

    pre = diagnostic["preSegmentation"]
    if not isinstance(pre, dict):
        problems.append(f"{where}.preSegmentation: object required")
    else:
        if not isinstance(pre.get("reliable"), bool):
            problems.append(f"{where}.preSegmentation.reliable: boolean required")
        if not isinstance(pre.get("reason"), str):
            problems.append(f"{where}.preSegmentation.reason: string required")
        box_count = pre.get("boxCount")
        if type(box_count) is not int or box_count < 0:
            problems.append(f"{where}.preSegmentation.boxCount: nonnegative integer required")

    segmentation = diagnostic["segmentation"]
    if not isinstance(segmentation, dict):
        problems.append(f"{where}.segmentation: object required")
    else:
        if not isinstance(segmentation.get("reliable"), bool):
            problems.append(f"{where}.segmentation.reliable: boolean required")
        if not isinstance(segmentation.get("reason"), str):
            problems.append(f"{where}.segmentation.reason: string required")

    if not isinstance(diagnostic["recoveryReason"], str):
        problems.append(f"{where}.recoveryReason: string required")
    if diagnostic["fallbackReason"] is not None and not isinstance(
        diagnostic["fallbackReason"], str
    ):
        problems.append(f"{where}.fallbackReason: string or null required")
    if not isinstance(diagnostic["usedPanelEvidence"], bool):
        problems.append(f"{where}.usedPanelEvidence: boolean required")

    assignments = diagnostic["assignments"]
    assignment_ids: list[str] = []
    if not isinstance(assignments, list):
        problems.append(f"{where}.assignments: array required")
    else:
        for index, item in enumerate(assignments):
            region_id = _validate_assignment(item, f"{where}.assignments[{index}]", problems)
            if region_id is not None:
                assignment_ids.append(region_id)
        if len(set(assignment_ids)) != len(assignment_ids):
            problems.append(f"{where}.assignments: duplicate regionId")

    relation_edges = diagnostic["relationEdges"]
    if not isinstance(relation_edges, list):
        problems.append(f"{where}.relationEdges: array required")
    else:
        for index, item in enumerate(relation_edges):
            _validate_relation_edge(item, f"{where}.relationEdges[{index}]", problems)

    fallback_order = diagnostic["fallbackOrder"]
    final_order = diagnostic["finalOrder"]
    if not _is_string_list(fallback_order):
        problems.append(f"{where}.fallbackOrder: string array required")
    if not _is_string_list(final_order):
        problems.append(f"{where}.finalOrder: string array required")
    if _is_string_list(fallback_order) and _is_string_list(final_order):
        if len(set(fallback_order)) != len(fallback_order):
            problems.append(f"{where}.fallbackOrder: duplicate region ID")
        if len(set(final_order)) != len(final_order):
            problems.append(f"{where}.finalOrder: duplicate region ID")
        if set(fallback_order) != set(final_order):
            problems.append(f"{where}: fallbackOrder/finalOrder region sets differ")
        pair_region_ids = {
            region_id
            for pair in page.qualification_pairs
            for region_id in (pair.earlier, pair.later)
        }
        if not pair_region_ids.issubset(set(final_order)):
            problems.append(f"{where}.finalOrder: qualification-pair region missing")

    directions = diagnostic["regionDirections"]
    if not isinstance(directions, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in directions.items()
    ):
        problems.append(f"{where}.regionDirections: string-to-string object required")
    elif _is_string_list(final_order) and set(directions) != set(final_order):
        problems.append(f"{where}: regionDirections/finalOrder region sets differ")

    if _is_string_list(final_order) and not set(assignment_ids).issubset(set(final_order)):
        problems.append(f"{where}.assignments: regionId outside finalOrder")
    return execution_sha if isinstance(execution_sha, str) else None


def validate_diagnostics_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> None:
    """Validate the v3 evaluator boundary before any v2 predicate delegation."""

    problems: list[str] = []
    required = _required_arms_by_page(annotations)
    page_by_id = {page.page_id: page for page in annotations}
    execution_shas: set[str] = set()
    for page_id, arms in required.items():
        page = page_by_id[page_id]
        for arm in sorted(arms, key=lambda value: value.value):
            arm_pages = diagnostics.get(arm)
            if not isinstance(arm_pages, dict):
                problems.append(f"diagnostics[{arm.value}]: required arm mapping missing/malformed")
                continue
            if page_id not in arm_pages:
                problems.append(f"diagnostics[{arm.value}][{page_id}]: required page missing")
                continue
            execution_sha = _validate_diagnostic(
                diagnostic=arm_pages[page_id],
                arm=arm,
                page=page,
                problems=problems,
            )
            if execution_sha is not None:
                execution_shas.add(execution_sha)
    if len(execution_shas) > 1:
        problems.append("diagnostics: required pages/arms use inconsistent executionSha values")
    if problems:
        raise V3DiagnosticValidationError(problems)


def _count_pages(page_ids: set[str]) -> ExerciseCount:
    ordered = tuple(sorted(page_ids))
    return ExerciseCount(len(ordered), (), ordered)


def _c3_generic_rejects(diagnostic: dict[str, object]) -> bool:
    pre = diagnostic["preSegmentation"]
    reason = diagnostic["recoveryReason"]
    assert isinstance(pre, dict)
    return (
        pre["reason"] == "fewer-than-two-groups"
        and pre["boxCount"] == 1
        and isinstance(reason, str)
        and reason.startswith("rejected-")
        and diagnostic["assignments"] == []
        and diagnostic["relationEdges"] == []
        and diagnostic["finalOrder"] == diagnostic["fallbackOrder"]
        and diagnostic["usedPanelEvidence"] is False
    )


def _c3_rejection_pages(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> set[str]:
    pages: set[str] = set()
    arms = (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    for page in annotations:
        has_negative_slice = any(
            C3_NEGATIVE_SLICES.intersection(pair.slices) for pair in page.qualification_pairs
        )
        if not has_negative_slice:
            continue
        if any(_c3_generic_rejects(diagnostics[arm][page.page_id]) for arm in arms):
            pages.add(page.page_id)
    return pages


def build_exercise_report_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> ExerciseReport:
    """Build the frozen v3 reachability report through a strict diagnostic boundary.

    Valid diagnostics delegate positive C1/C2/C3 and B1 predicates byte-for-byte to
    the consumed v2 evaluator. Generic C3 rejection is page-level because the frozen
    candidate exposes only a page-level rejection witness; one diagnostic therefore
    cannot be multiplied across multiple qualification pairs on the same page.
    """

    validate_diagnostics_v3(annotations=annotations, diagnostics=diagnostics)
    v2 = build_exercise_report(annotations=annotations, diagnostics=diagnostics)
    counts = {
        name: v2.counts[name]
        for name in EXERCISE_MINIMA_V3
        if name != "c3_rejection_pages"
    }
    counts["c3_rejection_pages"] = _count_pages(_c3_rejection_pages(annotations, diagnostics))
    return ExerciseReport(counts=counts, minima=dict(EXERCISE_MINIMA_V3))


def exercise_minimum_met_v3(report: ExerciseReport, name: str) -> bool:
    return report.counts[name].count >= report.minima[name]
