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

_DIAGNOSTIC_FIELDS = frozenset(
    {
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
)
_PRE_FIELDS = frozenset({"reliable", "reason", "boxCount", "boxes"})
_SEG_FIELDS = frozenset({"reliable", "reason", "boxes"})
_BOX_FIELDS = frozenset({"x1", "y1", "x2", "y2"})
_ASSIGNMENT_FIELDS = frozenset(
    {
        "regionId",
        "sourceIndex",
        "candidateGroupIndices",
        "status",
        "reason",
        "assignedGroupIndex",
        "uncertainNodeLabel",
    }
)
_EDGE_FIELDS = frozenset({"sourceNode", "targetNode", "rule"})
_INTEGRITY_FIELDS = frozenset(
    {
        "countPreserved",
        "objectIdentitySetPreserved",
        "contentConfidenceGeometryPreserved",
    }
)
_MAX_PANEL_GROUPS = 16
_DIRECT_FAILURE_REASONS = frozenset(
    {
        "fewer-than-two-groups",
        "too-many-groups",
        "group-too-small",
        "group-too-narrow",
        "group-too-short",
        "nested-or-inset-evidence",
        "ambiguous-overlap",
    }
)
_C3_ACCEPT = "accepted-strong-anchor-plus-occlusion-supported-frame"
_C3_REJECTIONS = frozenset(
    {
        "rejected-insufficient-long-frame-sides",
        "rejected-multiple-strong-frame-candidates",
        "rejected-no-unique-strong-frame-anchor",
        "rejected-ambiguous-or-missing-occlusion-supported-frame",
        "rejected-group-too-small",
        "rejected-group-too-narrow",
        "rejected-group-too-short",
        "rejected-nested-or-inset-evidence",
        "rejected-ambiguous-overlap",
    }
)
_POST_SEGMENTATION_FALLBACKS = frozenset(
    {
        "insufficient-confident-panel-groups",
        "precedence-cycle",
        "uncertain-relation-conflict",
    }
)
_RELATION_RULES = frozenset(
    {
        "unique-gutter-between-hard-panels",
        "validated-overlap-bridge-right-before-left",
        "uncertain-same-level-right-before-left",
        "uncertain-aligned-top-before-bottom",
        "uncertain-nonoverlap-top-before-bottom",
    }
)


class V3DiagnosticValidationError(ValueError):
    """Classified v3 diagnostic boundary failure."""

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


def _exact_fields(
    value: dict[object, object],
    expected: frozenset[str],
    where: str,
    problems: list[str],
) -> bool:
    actual = set(value)
    if not all(isinstance(key, str) for key in actual):
        problems.append(f"{where}: field names must be strings")
        return False
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        problems.append(f"{where}: field set mismatch missing={missing} extra={extra}")
        return False
    return True


def _boxes(
    value: object, where: str, problems: list[str]
) -> tuple[tuple[int, int, int, int], ...] | None:
    if not isinstance(value, list):
        problems.append(f"{where}: box array required")
        return None
    result: list[tuple[int, int, int, int]] = []
    valid = True
    for index, item in enumerate(value):
        item_where = f"{where}[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{item_where}: box object required")
            valid = False
            continue
        if not _exact_fields(item, _BOX_FIELDS, item_where, problems):
            valid = False
            continue
        raw = tuple(item[field] for field in ("x1", "y1", "x2", "y2"))
        if not all(type(coordinate) is int for coordinate in raw):
            problems.append(f"{item_where}: integer x1/y1/x2/y2 required")
            valid = False
            continue
        x1, y1, x2, y2 = raw
        assert all(isinstance(coordinate, int) for coordinate in raw)
        if not (0 <= x1 < x2 and 0 <= y1 < y2):
            problems.append(f"{item_where}: positive axis-aligned box required")
            valid = False
            continue
        result.append((x1, y1, x2, y2))
    parsed = tuple(result)
    if valid and parsed != tuple(
        sorted(parsed, key=lambda box: (box[1], box[0], box[3], box[2]))
    ):
        problems.append(f"{where}: boxes must use production y1/x1/y2/x2 order")
        valid = False
    return parsed if valid else None


def _direct_segmentation_state(
    reliable: bool, reason: str, count: int, where: str, problems: list[str]
) -> None:
    if reliable:
        if reason != "reliable":
            problems.append(f"{where}: reliable direct segmentation must use reason 'reliable'")
        if not 2 <= count <= _MAX_PANEL_GROUPS:
            problems.append(f"{where}: reliable direct segmentation requires 2..16 boxes")
        return
    if reason not in _DIRECT_FAILURE_REASONS:
        problems.append(f"{where}: unsupported direct segmentation failure reason")
    elif reason == "fewer-than-two-groups" and count >= 2:
        problems.append(f"{where}: fewer-than-two-groups requires fewer than two boxes")
    elif reason == "too-many-groups" and count <= _MAX_PANEL_GROUPS:
        problems.append(f"{where}: too-many-groups requires more than 16 boxes")
    elif reason not in {"fewer-than-two-groups", "too-many-groups"} and not (
        2 <= count <= _MAX_PANEL_GROUPS
    ):
        problems.append(f"{where}: geometry failure reason requires 2..16 boxes")


def _segmentation(
    value: object,
    where: str,
    problems: list[str],
    *,
    pre: bool,
) -> tuple[bool, str, tuple[tuple[int, int, int, int], ...]] | None:
    expected = _PRE_FIELDS if pre else _SEG_FIELDS
    if not isinstance(value, dict):
        problems.append(f"{where}: object required")
        return None
    if not _exact_fields(value, expected, where, problems):
        return None
    reliable = value["reliable"]
    reason = value["reason"]
    parsed_boxes = _boxes(value["boxes"], f"{where}.boxes", problems)
    if type(reliable) is not bool:
        problems.append(f"{where}.reliable: boolean required")
        return None
    if not isinstance(reason, str):
        problems.append(f"{where}.reason: string required")
        return None
    if parsed_boxes is None:
        return None
    if pre:
        count = value["boxCount"]
        if type(count) is not int or count < 0:
            problems.append(f"{where}.boxCount: nonnegative integer required")
            return None
        if count != len(parsed_boxes):
            problems.append(f"{where}: boxCount must equal len(boxes)")
        _direct_segmentation_state(reliable, reason, count, where, problems)
    elif reliable and reason == "recovered-merged-frame":
        if len(parsed_boxes) != 2:
            problems.append(f"{where}: recovered-merged-frame requires exactly two boxes")
    else:
        _direct_segmentation_state(
            reliable, reason, len(parsed_boxes), where, problems
        )
    return reliable, reason, parsed_boxes


def _assignment_reason(arm: ArmId, status: str) -> str:
    guarded = arm.c1
    if status == "confident":
        return (
            "unique-guarded-center-containment"
            if guarded
            else "unique-center-containment"
        )
    if status == "ambiguous":
        return (
            "multiple-guarded-center-containment"
            if guarded
            else "multiple-center-containment"
        )
    return "no-guarded-center-containment" if guarded else "no-center-containment"


def _assignments(
    value: object,
    *,
    arm: ArmId,
    expected_regions: set[str],
    segmentation: tuple[bool, str, tuple[tuple[int, int, int, int], ...]] | None,
    where: str,
    problems: list[str],
) -> list[dict[str, object]] | None:
    if not isinstance(value, list):
        problems.append(f"{where}: array required")
        return None
    if segmentation is None:
        return None
    reliable, _reason, boxes = segmentation
    if not reliable:
        if value:
            problems.append(f"{where}: unreliable segmentation must have no assignments")
            return None
        return []

    parsed: list[dict[str, object]] = []
    valid = True
    for position, item in enumerate(value):
        item_where = f"{where}[{position}]"
        if not isinstance(item, dict):
            problems.append(f"{item_where}: assignment object required")
            valid = False
            continue
        if not _exact_fields(item, _ASSIGNMENT_FIELDS, item_where, problems):
            valid = False
            continue
        region_id = item["regionId"]
        source_index = item["sourceIndex"]
        candidates = item["candidateGroupIndices"]
        status = item["status"]
        reason = item["reason"]
        assigned = item["assignedGroupIndex"]
        uncertain = item["uncertainNodeLabel"]
        if not isinstance(region_id, str) or not region_id:
            problems.append(f"{item_where}.regionId: nonempty string required")
            valid = False
        if type(source_index) is not int or source_index != position:
            problems.append(
                f"{item_where}.sourceIndex: must equal serializer assignment position"
            )
            valid = False
        if not isinstance(candidates, list) or not all(
            type(index) is int for index in candidates
        ):
            problems.append(f"{item_where}.candidateGroupIndices: integer array required")
            valid = False
            continue
        if candidates != sorted(set(candidates)):
            problems.append(
                f"{item_where}.candidateGroupIndices: unique increasing indices required"
            )
            valid = False
        if any(index < 0 or index >= len(boxes) for index in candidates):
            problems.append(
                f"{item_where}.candidateGroupIndices: index outside segmentation boxes"
            )
            valid = False
        if not isinstance(status, str) or status not in {
            "confident",
            "unassigned",
            "ambiguous",
        }:
            problems.append(f"{item_where}.status: unsupported assignment status")
            valid = False
            continue
        if reason != _assignment_reason(arm, status):
            problems.append(f"{item_where}.reason: inconsistent with arm/status")
            valid = False
        if assigned is not None and (
            type(assigned) is not int or assigned < 0 or assigned >= len(boxes)
        ):
            problems.append(
                f"{item_where}.assignedGroupIndex: segmentation-box index or null required"
            )
            valid = False
        if uncertain is not None and not isinstance(uncertain, str):
            problems.append(f"{item_where}.uncertainNodeLabel: string or null required")
            valid = False
        expected_uncertain = f"u{position:03d}"
        if status == "confident":
            if len(candidates) != 1:
                problems.append(
                    f"{item_where}: confident assignment requires exactly one candidate"
                )
                valid = False
            elif assigned != candidates[0]:
                problems.append(
                    f"{item_where}: assignedGroupIndex must equal sole candidate"
                )
                valid = False
            if uncertain is not None:
                problems.append(
                    f"{item_where}: confident assignment cannot have uncertain node"
                )
                valid = False
        elif status == "ambiguous":
            if len(candidates) < 2:
                problems.append(
                    f"{item_where}: ambiguous assignment requires multiple candidates"
                )
                valid = False
            if assigned is not None or uncertain != expected_uncertain:
                problems.append(
                    f"{item_where}: ambiguous assignment serializer invariant violated"
                )
                valid = False
        elif candidates or assigned is not None or uncertain != expected_uncertain:
            problems.append(
                f"{item_where}: unassigned assignment serializer invariant violated"
            )
            valid = False
        parsed.append(item)
    region_ids = [
        item["regionId"] for item in parsed if isinstance(item.get("regionId"), str)
    ]
    if len(value) != len(expected_regions):
        problems.append(f"{where}: reliable segmentation requires one assignment per region")
        valid = False
    if len(set(region_ids)) != len(region_ids):
        problems.append(f"{where}: duplicate regionId")
        valid = False
    if len(parsed) == len(value) and set(region_ids) != expected_regions:
        problems.append(f"{where}: assignment region set must equal full page region set")
        valid = False
    return parsed if valid else None


def _region_order(
    value: object, where: str, expected: set[str], problems: list[str]
) -> tuple[str, ...] | None:
    if not _is_string_list(value):
        problems.append(f"{where}: string array required")
        return None
    if len(set(value)) != len(value):
        problems.append(f"{where}: duplicate region ID")
        return None
    if set(value) != expected:
        problems.append(f"{where}: must be exact permutation of full page region set")
        return None
    return tuple(value)


def _node_sets(
    segmentation: tuple[bool, str, tuple[tuple[int, int, int, int], ...]] | None,
    assignments: list[dict[str, object]] | None,
) -> tuple[set[str], set[str], set[str]] | None:
    if segmentation is None or assignments is None or not segmentation[0]:
        return None
    groups = {f"g{index:03d}" for index in range(len(segmentation[2]))}
    uncertain = {
        str(item["uncertainNodeLabel"])
        for item in assignments
        if isinstance(item.get("uncertainNodeLabel"), str)
    }
    return groups, uncertain, groups | uncertain


def _node_order(
    value: object,
    *,
    used: bool | None,
    vocabulary: set[str] | None,
    where: str,
    problems: list[str],
) -> tuple[str, ...] | None:
    if not _is_string_list(value):
        problems.append(f"{where}: string array required")
        return None
    if len(set(value)) != len(value):
        problems.append(f"{where}: duplicate node")
        return None
    if used is False and value:
        problems.append(f"{where}: fallback diagnostic must have empty nodeOrder")
    if used is True and vocabulary is not None and set(value) != vocabulary:
        problems.append(
            f"{where}: success nodeOrder must cover exact materialized node vocabulary"
        )
    return tuple(value)


def _relation_edges(
    value: object,
    *,
    arm: ArmId,
    assignments: list[dict[str, object]] | None,
    node_sets: tuple[set[str], set[str], set[str]] | None,
    node_order: tuple[str, ...] | None,
    used: bool | None,
    where: str,
    problems: list[str],
) -> list[tuple[str, str, str]] | None:
    if not isinstance(value, list):
        problems.append(f"{where}: array required")
        return None
    parsed: list[tuple[str, str, str]] = []
    valid = True
    groups: set[str] = set()
    uncertain: set[str] = set()
    vocabulary: set[str] = set()
    if node_sets is not None:
        groups, uncertain, vocabulary = node_sets
    for index, item in enumerate(value):
        item_where = f"{where}[{index}]"
        if not isinstance(item, dict):
            problems.append(f"{item_where}: relation edge object required")
            valid = False
            continue
        if not _exact_fields(item, _EDGE_FIELDS, item_where, problems):
            valid = False
            continue
        source, target, rule = (
            item["sourceNode"],
            item["targetNode"],
            item["rule"],
        )
        if not all(isinstance(part, str) and part for part in (source, target, rule)):
            problems.append(
                f"{item_where}: nonempty sourceNode/targetNode/rule strings required"
            )
            valid = False
            continue
        assert isinstance(source, str)
        assert isinstance(target, str)
        assert isinstance(rule, str)
        if rule not in _RELATION_RULES:
            problems.append(f"{item_where}.rule: unsupported production relation rule")
            valid = False
        if node_sets is not None:
            if source not in vocabulary or target not in vocabulary:
                problems.append(f"{item_where}: relation endpoint outside node vocabulary")
                valid = False
            if not (
                (source in groups and target in uncertain)
                or (source in uncertain and target in groups)
            ):
                problems.append(
                    f"{item_where}: relation diagnostic must connect panel and uncertain nodes"
                )
                valid = False
        if source == target:
            problems.append(f"{item_where}: self relation impossible")
            valid = False
        parsed.append((source, target, rule))
    if parsed and not arm.c2:
        problems.append(f"{where}: relation diagnostics require a C2-enabled arm")
        valid = False
    if len(set(parsed)) != len(parsed):
        problems.append(f"{where}: duplicate relation edge")
        valid = False

    if node_sets is not None and assignments is not None:
        assignment_by_node = {
            item["uncertainNodeLabel"]: item
            for item in assignments
            if isinstance(item.get("uncertainNodeLabel"), str)
        }
        for uncertain_node in uncertain:
            node_edges = [
                edge
                for edge in parsed
                if edge[0] == uncertain_node or edge[1] == uncertain_node
            ]
            if not node_edges:
                continue
            rules = {edge[2] for edge in node_edges}
            incoming = [edge for edge in node_edges if edge[1] == uncertain_node]
            outgoing = [edge for edge in node_edges if edge[0] == uncertain_node]
            special = rules & {
                "unique-gutter-between-hard-panels",
                "validated-overlap-bridge-right-before-left",
            }
            if special:
                panel_nodes = {
                    source if source in groups else target
                    for source, target, _rule in node_edges
                }
                if (
                    len(special) != 1
                    or rules != special
                    or len(node_edges) != 2
                    or len(incoming) != 1
                    or len(outgoing) != 1
                    or len(panel_nodes) != 2
                ):
                    problems.append(
                        f"{where}: gutter/overlap relation must be an exact two-edge bracket"
                    )
                    valid = False
                if "validated-overlap-bridge-right-before-left" in rules:
                    assignment = assignment_by_node.get(uncertain_node)
                    if assignment is None or not (
                        assignment.get("status") == "ambiguous"
                        and isinstance(assignment.get("candidateGroupIndices"), list)
                        and len(assignment["candidateGroupIndices"]) == 2
                    ):
                        problems.append(
                            f"{where}: overlap bridge requires a two-candidate ambiguous assignment"
                        )
                        valid = False
            elif not incoming or not outgoing:
                problems.append(
                    f"{where}: uncertain precedence relations require two-sided evidence"
                )
                valid = False

    if used is True and node_order is not None:
        positions = {node: index for index, node in enumerate(node_order)}
        for index, (source, target, _rule) in enumerate(parsed):
            if (
                source in positions
                and target in positions
                and positions[source] >= positions[target]
            ):
                problems.append(
                    f"{where}[{index}]: relation edge contradicts materialized nodeOrder"
                )
                valid = False
    return parsed if valid else None


def _recovery_state(
    *,
    arm: ArmId,
    pre: tuple[bool, str, tuple[tuple[int, int, int, int], ...]] | None,
    final: tuple[bool, str, tuple[tuple[int, int, int, int], ...]] | None,
    reason: object,
    where: str,
    problems: list[str],
) -> None:
    if not isinstance(reason, str):
        problems.append(f"{where}.recoveryReason: string required")
        return
    if pre is None or final is None:
        return
    same = pre == final
    if not arm.c3:
        if reason != "disabled":
            problems.append(f"{where}.recoveryReason: non-C3 arm must use 'disabled'")
        if not same:
            problems.append(f"{where}: non-C3 final segmentation must equal preSegmentation")
        return
    if pre[0]:
        if reason != "not-needed":
            problems.append(
                f"{where}.recoveryReason: reliable C3 pre-state must use 'not-needed'"
            )
        if not same:
            problems.append(f"{where}: not-needed recovery must preserve segmentation")
        return
    eligible = pre[1] == "fewer-than-two-groups" and len(pre[2]) == 1
    if not eligible:
        if reason != "not-eligible":
            problems.append(
                f"{where}.recoveryReason: ineligible C3 pre-state must use 'not-eligible'"
            )
        if not same:
            problems.append(f"{where}: not-eligible recovery must preserve segmentation")
    elif reason == _C3_ACCEPT:
        if not (final[0] and final[1] == "recovered-merged-frame" and len(final[2]) == 2):
            problems.append(f"{where}: accepted C3 recovery requires recovered two-box state")
    elif reason in _C3_REJECTIONS:
        if not same:
            problems.append(f"{where}: rejected C3 recovery must preserve preSegmentation")
    else:
        problems.append(f"{where}.recoveryReason: unsupported eligible C3 recovery reason")


def _execution_state(
    *,
    arm: ArmId,
    segmentation: tuple[bool, str, tuple[tuple[int, int, int, int], ...]] | None,
    assignments: list[dict[str, object]] | None,
    edges: list[tuple[str, str, str]] | None,
    node_order: tuple[str, ...] | None,
    fallback_reason: object,
    used: object,
    fallback_order: tuple[str, ...] | None,
    final_order: tuple[str, ...] | None,
    where: str,
    problems: list[str],
) -> None:
    if fallback_reason is not None and not isinstance(fallback_reason, str):
        problems.append(f"{where}.fallbackReason: string or null required")
        return
    if type(used) is not bool:
        problems.append(f"{where}.usedPanelEvidence: boolean required")
        return
    if segmentation is None:
        return
    reliable, segmentation_reason, _boxes_value = segmentation
    if not reliable:
        if used:
            problems.append(f"{where}: unreliable segmentation cannot use panel evidence")
        if fallback_reason != segmentation_reason:
            problems.append(f"{where}: unreliable segmentation fallbackReason must equal reason")
        if assignments not in ([], None):
            problems.append(f"{where}: unreliable segmentation cannot have assignments")
        if edges not in ([], None):
            problems.append(f"{where}: unreliable segmentation cannot have relation edges")
        if node_order not in ((), None):
            problems.append(f"{where}: unreliable segmentation cannot have nodeOrder")
        if fallback_order is not None and final_order != fallback_order:
            problems.append(f"{where}: fallback finalOrder must equal fallbackOrder")
        return
    if assignments is None:
        return
    confident_groups = {
        item["assignedGroupIndex"]
        for item in assignments
        if isinstance(item.get("assignedGroupIndex"), int)
    }
    if used:
        if fallback_reason is not None:
            problems.append(f"{where}: successful diagnostic must have null fallbackReason")
        if len(confident_groups) < 2:
            problems.append(f"{where}: panel-evidence success requires two confident groups")
        return
    if fallback_reason not in _POST_SEGMENTATION_FALLBACKS:
        problems.append(f"{where}.fallbackReason: unsupported reliable-segmentation fallback")
    if node_order not in ((), None):
        problems.append(f"{where}: fallback diagnostic must have empty nodeOrder")
    if fallback_order is not None and final_order != fallback_order:
        problems.append(f"{where}: fallback finalOrder must equal fallbackOrder")
    if fallback_reason == "insufficient-confident-panel-groups":
        if len(confident_groups) >= 2:
            problems.append(f"{where}: insufficient-groups fallback has two confident groups")
        if edges not in ([], None):
            problems.append(f"{where}: insufficient-groups fallback cannot have relation edges")
    elif fallback_reason == "precedence-cycle" and len(confident_groups) < 2:
        problems.append(f"{where}: precedence-cycle requires two confident groups")
    elif fallback_reason == "uncertain-relation-conflict":
        if not arm.c2:
            problems.append(f"{where}: uncertain relation conflict requires C2-enabled arm")
        if len(confident_groups) < 2:
            problems.append(f"{where}: uncertain relation conflict requires two confident groups")


def _diagnostic(
    diagnostic: object,
    *,
    arm: ArmId,
    page: PageGroundTruth,
    problems: list[str],
) -> str | None:
    where = f"diagnostics[{arm.value}][{page.page_id}]"
    if not isinstance(diagnostic, dict):
        problems.append(f"{where}: diagnostic object required")
        return None
    if not _exact_fields(diagnostic, _DIAGNOSTIC_FIELDS, where, problems):
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

    expected_regions = set(page.reading_order) | set(page.unscored_region_ids)
    pre = _segmentation(
        diagnostic["preSegmentation"], f"{where}.preSegmentation", problems, pre=True
    )
    final_segmentation = _segmentation(
        diagnostic["segmentation"], f"{where}.segmentation", problems, pre=False
    )
    _recovery_state(
        arm=arm,
        pre=pre,
        final=final_segmentation,
        reason=diagnostic["recoveryReason"],
        where=where,
        problems=problems,
    )
    assignments = _assignments(
        diagnostic["assignments"],
        arm=arm,
        expected_regions=expected_regions,
        segmentation=final_segmentation,
        where=f"{where}.assignments",
        problems=problems,
    )
    node_sets = _node_sets(final_segmentation, assignments)
    used_raw = diagnostic["usedPanelEvidence"]
    used = used_raw if type(used_raw) is bool else None
    node_order = _node_order(
        diagnostic["nodeOrder"],
        used=used,
        vocabulary=node_sets[2] if node_sets is not None else None,
        where=f"{where}.nodeOrder",
        problems=problems,
    )
    edges = _relation_edges(
        diagnostic["relationEdges"],
        arm=arm,
        assignments=assignments,
        node_sets=node_sets,
        node_order=node_order,
        used=used,
        where=f"{where}.relationEdges",
        problems=problems,
    )
    fallback_order = _region_order(
        diagnostic["fallbackOrder"],
        f"{where}.fallbackOrder",
        expected_regions,
        problems,
    )
    final_order = _region_order(
        diagnostic["finalOrder"], f"{where}.finalOrder", expected_regions, problems
    )

    directions = diagnostic["regionDirections"]
    if not isinstance(directions, dict):
        problems.append(f"{where}.regionDirections: object required")
    else:
        if set(directions) != expected_regions:
            problems.append(f"{where}.regionDirections: keys must equal full page region set")
        for region_id, direction in directions.items():
            if (
                not isinstance(region_id, str)
                or not isinstance(direction, str)
                or direction not in {"h", "v"}
            ):
                problems.append(
                    f"{where}.regionDirections[{region_id!r}]: "
                    "production fixture direction must be h or v"
                )

    integrity = diagnostic["regionIntegrity"]
    if not isinstance(integrity, dict):
        problems.append(f"{where}.regionIntegrity: object required")
    elif _exact_fields(integrity, _INTEGRITY_FIELDS, f"{where}.regionIntegrity", problems):
        for field in sorted(_INTEGRITY_FIELDS):
            if integrity[field] is not True:
                problems.append(
                    f"{where}.regionIntegrity.{field}: production serializer requires true"
                )

    _execution_state(
        arm=arm,
        segmentation=final_segmentation,
        assignments=assignments,
        edges=edges,
        node_order=node_order,
        fallback_reason=diagnostic["fallbackReason"],
        used=diagnostic["usedPanelEvidence"],
        fallback_order=fallback_order,
        final_order=final_order,
        where=where,
        problems=problems,
    )
    return execution_sha if isinstance(execution_sha, str) else None


def _cross_arm_equal(
    states: list[tuple[ArmId, object]],
    page_id: str,
    field: str,
    problems: list[str],
) -> None:
    if len(states) < 2:
        return
    first_arm, first_value = states[0]
    for arm, value in states[1:]:
        if value != first_value:
            problems.append(
                f"diagnostics[{page_id}]: {field} differs across "
                f"{first_arm.value}/{arm.value} despite shared production input"
            )


def validate_diagnostics_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: object,
) -> None:
    """Validate the v3 evaluator boundary before any v2 predicate delegation."""

    if not isinstance(diagnostics, dict):
        raise V3DiagnosticValidationError(
            ("diagnostics: top-level arm mapping object required",)
        )
    problems: list[str] = []
    required = _required_arms_by_page(annotations)
    page_by_id = {page.page_id: page for page in annotations}
    execution_shas: set[str] = set()
    for page_id, arms in required.items():
        page = page_by_id[page_id]
        pre_states: list[tuple[ArmId, object]] = []
        fallback_orders: list[tuple[ArmId, object]] = []
        directions: list[tuple[ArmId, object]] = []
        c3_states: list[tuple[ArmId, object]] = []
        for arm in sorted(arms, key=lambda value: value.value):
            arm_pages = diagnostics.get(arm)
            if not isinstance(arm_pages, dict):
                problems.append(
                    f"diagnostics[{arm.value}]: required arm mapping missing/malformed"
                )
                continue
            if page_id not in arm_pages:
                problems.append(f"diagnostics[{arm.value}][{page_id}]: required page missing")
                continue
            diagnostic = arm_pages[page_id]
            execution_sha = _diagnostic(
                diagnostic, arm=arm, page=page, problems=problems
            )
            if execution_sha is not None:
                execution_shas.add(execution_sha)
            if isinstance(diagnostic, dict):
                pre_states.append((arm, diagnostic.get("preSegmentation")))
                fallback_orders.append((arm, diagnostic.get("fallbackOrder")))
                directions.append((arm, diagnostic.get("regionDirections")))
                if arm.c3:
                    c3_states.append(
                        (
                            arm,
                            (
                                diagnostic.get("segmentation"),
                                diagnostic.get("recoveryReason"),
                            ),
                        )
                    )
        _cross_arm_equal(pre_states, page_id, "preSegmentation", problems)
        _cross_arm_equal(fallback_orders, page_id, "fallbackOrder", problems)
        _cross_arm_equal(directions, page_id, "regionDirections", problems)
        _cross_arm_equal(c3_states, page_id, "C3 segmentation/recoveryReason", problems)
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
        if has_negative_slice and any(
            _c3_generic_rejects(diagnostics[arm][page.page_id]) for arm in arms
        ):
            pages.add(page.page_id)
    return pages


def build_exercise_report_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> ExerciseReport:
    """Build the frozen v3 reachability report through a strict diagnostic boundary."""

    validate_diagnostics_v3(annotations=annotations, diagnostics=diagnostics)
    v2 = build_exercise_report(annotations=annotations, diagnostics=diagnostics)
    counts = {
        name: v2.counts[name]
        for name in EXERCISE_MINIMA_V3
        if name != "c3_rejection_pages"
    }
    counts["c3_rejection_pages"] = _count_pages(
        _c3_rejection_pages(annotations, diagnostics)
    )
    return ExerciseReport(counts=counts, minima=dict(EXERCISE_MINIMA_V3))


def exercise_minimum_met_v3(report: ExerciseReport, name: str) -> bool:
    return report.counts[name].count >= report.minima[name]
