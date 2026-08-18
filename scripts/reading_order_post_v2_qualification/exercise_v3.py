from __future__ import annotations

import re

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

_EXECUTION_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

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
    **{
        slice_name: frozenset({ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1})
        for slice_name in C3_NEGATIVE_SLICES
    },
    "b1-horizontal": frozenset({ArmId.B1_ONLY, ArmId.C1_C2_C3_B1}),
    "b1-vertical": frozenset({ArmId.B1_ONLY, ArmId.C1_C2_C3_B1}),
    "b1-mixed-orientation": frozenset({ArmId.B1_ONLY, ArmId.C1_C2_C3_B1}),
}


class InvalidDiagnosticError(ValueError):
    """Raised when v3 diagnostic evidence is missing, malformed, or inconsistent."""


def _invalid(where: str, message: str) -> InvalidDiagnosticError:
    return InvalidDiagnosticError(f"{where}: {message}")


def _validate_box_list(value: object, where: str) -> None:
    if not isinstance(value, list):
        raise _invalid(where, "box list required")
    for index, box in enumerate(value):
        if not isinstance(box, dict):
            raise _invalid(f"{where}[{index}]", "box object required")
        if set(box) != {"x1", "y1", "x2", "y2"}:
            raise _invalid(f"{where}[{index}]", "box keys must be x1/y1/x2/y2")
        if not all(type(box[key]) is int for key in ("x1", "y1", "x2", "y2")):
            raise _invalid(f"{where}[{index}]", "box coordinates must be integers")


def _validate_string_list(value: object, where: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise _invalid(where, "string array required")
    return [str(item) for item in value]


def _validate_diagnostic(
    *,
    diagnostic: dict[str, object],
    arm: ArmId,
    page: PageGroundTruth,
) -> str:
    where = f"{arm.value}/{page.page_id}"
    required = {
        "schemaVersion",
        "experimentArm",
        "executionSha",
        "pageId",
        "preSegmentation",
        "segmentation",
        "recoveryReason",
        "assignments",
        "relationEdges",
        "nodeOrder",
        "fallbackReason",
        "usedPanelEvidence",
        "fallbackOrder",
        "finalOrder",
        "regionDirections",
        "regionIntegrity",
    }
    missing = required - set(diagnostic)
    if missing:
        raise _invalid(where, f"missing diagnostic fields: {sorted(missing)}")

    if diagnostic["schemaVersion"] != DIAGNOSTIC_SCHEMA_VERSION:
        raise _invalid(where, "wrong diagnostic schemaVersion")
    if diagnostic["experimentArm"] != arm.value:
        raise _invalid(where, "experimentArm does not match diagnostics arm key")
    if diagnostic["pageId"] != page.page_id:
        raise _invalid(where, "pageId does not match diagnostics page key")

    execution_sha = diagnostic["executionSha"]
    if not isinstance(execution_sha, str) or _EXECUTION_SHA_RE.fullmatch(execution_sha) is None:
        raise _invalid(where, "executionSha must be a lowercase 40-hex SHA")

    pre = diagnostic["preSegmentation"]
    if not isinstance(pre, dict):
        raise _invalid(where, "preSegmentation object required")
    if (
        not isinstance(pre.get("reliable"), bool)
        or not isinstance(pre.get("reason"), str)
        or type(pre.get("boxCount")) is not int
    ):
        raise _invalid(where, "malformed preSegmentation facts")
    _validate_box_list(pre.get("boxes"), f"{where}.preSegmentation.boxes")
    if pre["boxCount"] != len(pre["boxes"]):
        raise _invalid(where, "preSegmentation.boxCount disagrees with boxes")

    segmentation = diagnostic["segmentation"]
    if not isinstance(segmentation, dict):
        raise _invalid(where, "segmentation object required")
    if not isinstance(segmentation.get("reliable"), bool) or not isinstance(
        segmentation.get("reason"), str
    ):
        raise _invalid(where, "malformed segmentation facts")
    _validate_box_list(segmentation.get("boxes"), f"{where}.segmentation.boxes")

    if not isinstance(diagnostic["recoveryReason"], str):
        raise _invalid(where, "recoveryReason string required")
    if not isinstance(diagnostic["usedPanelEvidence"], bool):
        raise _invalid(where, "usedPanelEvidence boolean required")
    fallback_reason = diagnostic["fallbackReason"]
    if fallback_reason is not None and not isinstance(fallback_reason, str):
        raise _invalid(where, "fallbackReason must be string or null")

    directions = diagnostic["regionDirections"]
    if not isinstance(directions, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in directions.items()
    ):
        raise _invalid(where, "regionDirections must be a string-to-string object")
    expected_region_ids = set(page.reading_order) | set(page.unscored_region_ids)
    if set(directions) != expected_region_ids:
        raise _invalid(where, "regionDirections region set disagrees with annotation")

    fallback_order = _validate_string_list(diagnostic["fallbackOrder"], f"{where}.fallbackOrder")
    final_order = _validate_string_list(diagnostic["finalOrder"], f"{where}.finalOrder")
    for label, order in (("fallbackOrder", fallback_order), ("finalOrder", final_order)):
        if len(order) != len(expected_region_ids) or set(order) != expected_region_ids:
            raise _invalid(where, f"{label} must be a permutation of page region IDs")

    node_order = _validate_string_list(diagnostic["nodeOrder"], f"{where}.nodeOrder")
    if len(node_order) != len(set(node_order)):
        raise _invalid(where, "nodeOrder contains duplicates")

    assignments = diagnostic["assignments"]
    if not isinstance(assignments, list):
        raise _invalid(where, "assignments array required")
    seen_region_ids: set[str] = set()
    seen_source_indices: set[int] = set()
    for index, assignment in enumerate(assignments):
        item_where = f"{where}.assignments[{index}]"
        if not isinstance(assignment, dict):
            raise _invalid(item_where, "assignment object required")
        assignment_required = {
            "regionId",
            "sourceIndex",
            "candidateGroupIndices",
            "status",
            "reason",
            "assignedGroupIndex",
            "uncertainNodeLabel",
        }
        if assignment_required - set(assignment):
            raise _invalid(item_where, "missing assignment fields")
        region_id = assignment["regionId"]
        source_index = assignment["sourceIndex"]
        candidate_indices = assignment["candidateGroupIndices"]
        status = assignment["status"]
        reason = assignment["reason"]
        assigned_group_index = assignment["assignedGroupIndex"]
        uncertain_node_label = assignment["uncertainNodeLabel"]
        if not isinstance(region_id, str) or region_id not in expected_region_ids:
            raise _invalid(item_where, "regionId must identify a page region")
        if region_id in seen_region_ids:
            raise _invalid(item_where, "duplicate assignment regionId")
        seen_region_ids.add(region_id)
        if type(source_index) is not int or source_index < 0 or source_index in seen_source_indices:
            raise _invalid(item_where, "sourceIndex must be a unique non-negative integer")
        seen_source_indices.add(source_index)
        if not isinstance(candidate_indices, list) or not all(
            type(item) is int and item >= 0 for item in candidate_indices
        ):
            raise _invalid(item_where, "candidateGroupIndices must be non-negative integers")
        if not isinstance(status, str) or status not in {"confident", "ambiguous", "unassigned"}:
            raise _invalid(item_where, "unknown assignment status")
        if not isinstance(reason, str):
            raise _invalid(item_where, "assignment reason string required")
        if assigned_group_index is not None and (
            type(assigned_group_index) is not int or assigned_group_index < 0
        ):
            raise _invalid(item_where, "assignedGroupIndex must be non-negative integer or null")
        if assigned_group_index is None:
            if not isinstance(uncertain_node_label, str) or not uncertain_node_label:
                raise _invalid(item_where, "unassigned assignment requires uncertainNodeLabel")
        elif uncertain_node_label is not None:
            raise _invalid(item_where, "confident assignment must not carry uncertainNodeLabel")

    relation_edges = diagnostic["relationEdges"]
    if not isinstance(relation_edges, list):
        raise _invalid(where, "relationEdges array required")
    for index, edge in enumerate(relation_edges):
        edge_where = f"{where}.relationEdges[{index}]"
        if not isinstance(edge, dict):
            raise _invalid(edge_where, "edge object required")
        if set(edge) != {"sourceNode", "targetNode", "rule"} or not all(
            isinstance(edge.get(key), str) and bool(edge.get(key))
            for key in ("sourceNode", "targetNode", "rule")
        ):
            raise _invalid(edge_where, "edge requires non-empty sourceNode/targetNode/rule strings")

    integrity = diagnostic["regionIntegrity"]
    if not isinstance(integrity, dict):
        raise _invalid(where, "regionIntegrity object required")
    integrity_fields = {
        "countPreserved",
        "objectIdentitySetPreserved",
        "contentConfidenceGeometryPreserved",
    }
    if set(integrity) != integrity_fields or any(
        integrity[field] is not True for field in integrity_fields
    ):
        raise _invalid(where, "regionIntegrity must assert all production integrity checks")

    return execution_sha


def _required_arms(page: PageGroundTruth) -> frozenset[ArmId]:
    required: set[ArmId] = set()
    for pair in page.qualification_pairs:
        for slice_name in pair.slices:
            required.update(_SLICE_ARMS.get(slice_name, ()))
    return frozenset(required)


def _validate_diagnostics(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> None:
    execution_sha: str | None = None
    for page in annotations:
        for arm in _required_arms(page):
            arm_diagnostics = diagnostics.get(arm)
            if not isinstance(arm_diagnostics, dict):
                raise _invalid(page.page_id, f"missing diagnostics arm {arm.value}")
            diagnostic = arm_diagnostics.get(page.page_id)
            if not isinstance(diagnostic, dict):
                raise _invalid(page.page_id, f"missing diagnostic for arm {arm.value}")
            observed_sha = _validate_diagnostic(diagnostic=diagnostic, arm=arm, page=page)
            if execution_sha is None:
                execution_sha = observed_sha
            elif observed_sha != execution_sha:
                raise _invalid(page.page_id, "diagnostics mix executionSha values")


def _count_pages(page_ids: set[str]) -> ExerciseCount:
    ordered = tuple(sorted(page_ids))
    return ExerciseCount(len(ordered), (), ordered)


def _c3_generic_rejects(diagnostic: dict[str, object]) -> bool:
    pre = diagnostic["preSegmentation"]
    reason = diagnostic["recoveryReason"]
    return (
        isinstance(pre, dict)
        and pre["reason"] == "fewer-than-two-groups"
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
    page_ids: set[str] = set()
    arms = (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    for page in annotations:
        eligible = any(
            C3_NEGATIVE_SLICES.intersection(pair.slices)
            for pair in page.qualification_pairs
        )
        if eligible and any(_c3_generic_rejects(diagnostics[arm][page.page_id]) for arm in arms):
            page_ids.add(page.page_id)
    return page_ids


def build_exercise_report_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> ExerciseReport:
    """Build the frozen v3 reachability report through a strict diagnostic boundary.

    Any missing, malformed, or inconsistent diagnostic raises InvalidDiagnosticError
    before the frozen v2 evaluator is delegated to. A future v3 runner must classify
    that exception as INVALID_EXPERIMENT / harness-invalid rather than as a zero,
    false positive, or incidental KeyError.

    Positive C1/C2/C3 and B1 predicates remain delegated to the frozen v2 evaluator.
    C3 generic rejection is page-level because the candidate exposes only a page-level
    rejection witness; one page can therefore contribute at most one rejection count.
    """

    _validate_diagnostics(annotations, diagnostics)
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
