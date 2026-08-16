from __future__ import annotations

from dataclasses import dataclass

from .contracts import ArmId, EXERCISE_MINIMA, PageGroundTruth, QualificationPair


@dataclass(frozen=True, slots=True)
class ExerciseCount:
    count: int
    pair_ids: tuple[str, ...]
    page_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExerciseReport:
    counts: dict[str, ExerciseCount]
    minima: dict[str, int]


def _pair_key(page_id: str, pair: QualificationPair) -> str:
    return f"{page_id}:{pair.pair_id}"


def _pairs_for_slice(
    annotations: tuple[PageGroundTruth, ...], slice_name: str
) -> list[tuple[PageGroundTruth, QualificationPair]]:
    return [
        (page, pair)
        for page in annotations
        for pair in page.qualification_pairs
        if slice_name in pair.slices
    ]


def _assignment_by_region(diagnostic: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = diagnostic.get("assignments")
    if not isinstance(raw, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("regionId"), str):
            result[str(item["regionId"])] = item
    return result


def _relation_edges(diagnostic: dict[str, object]) -> list[dict[str, str]]:
    raw = diagnostic.get("relationEdges")
    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        source, target, rule = item.get("sourceNode"), item.get("targetNode"), item.get("rule")
        if all(isinstance(value, str) for value in (source, target, rule)):
            result.append({"sourceNode": source, "targetNode": target, "rule": rule})
    return result


def _count(records: set[tuple[str, str]]) -> ExerciseCount:
    pair_ids = tuple(sorted(f"{page_id}:{pair_id}" for page_id, pair_id in records))
    page_ids = tuple(sorted({page_id for page_id, _ in records}))
    return ExerciseCount(len(records), pair_ids, page_ids)


def _c1_records(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    for page, pair in _pairs_for_slice(annotations, "c1-boundary-positive"):
        control = _assignment_by_region(diagnostics[ArmId.CONTROL][page.page_id])
        guarded = _assignment_by_region(diagnostics[ArmId.C1_ONLY][page.page_id])
        exercised = False
        for region_id in (pair.earlier, pair.later):
            before, after = control.get(region_id), guarded.get(region_id)
            if before is None or after is None:
                continue
            if (
                before.get("status") == "confident"
                and after.get("status") in {"unassigned", "ambiguous"}
                and before.get("candidateGroupIndices") != after.get("candidateGroupIndices")
            ):
                exercised = True
        if exercised:
            records.add((page.page_id, pair.pair_id))
    return records


def _c2_rule_records(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
    *,
    slice_name: str,
    rule_kind: str,
) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    arms = (ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    for page, pair in _pairs_for_slice(annotations, slice_name):
        for arm in arms:
            diagnostic = diagnostics[arm][page.page_id]
            assignments = _assignment_by_region(diagnostic)
            labels = {
                str(assignment.get("uncertainNodeLabel"))
                for region_id, assignment in assignments.items()
                if region_id in {pair.earlier, pair.later}
                and isinstance(assignment.get("uncertainNodeLabel"), str)
            }
            if not labels:
                continue
            for edge in _relation_edges(diagnostic):
                if edge["sourceNode"] not in labels and edge["targetNode"] not in labels:
                    continue
                rule = edge["rule"]
                if (
                    (rule_kind == "gutter" and rule == "unique-gutter-between-hard-panels")
                    or (
                        rule_kind == "overlap"
                        and rule == "validated-overlap-bridge-right-before-left"
                    )
                    or (rule_kind == "pair-precedence" and rule.startswith("uncertain-"))
                ):
                    records.add((page.page_id, pair.pair_id))
                    break
            if (page.page_id, pair.pair_id) in records:
                break
    return records


def _c2_no_relation_records(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    for page, pair in _pairs_for_slice(annotations, "c2-one-sided-non-unique-fail-closed"):
        diagnostic = diagnostics[ArmId.C2_ONLY][page.page_id]
        assignments = _assignment_by_region(diagnostic)
        labels = {
            str(assignments[region_id].get("uncertainNodeLabel"))
            for region_id in (pair.earlier, pair.later)
            if region_id in assignments
            and isinstance(assignments[region_id].get("uncertainNodeLabel"), str)
        }
        if labels and not any(
            edge["sourceNode"] in labels or edge["targetNode"] in labels
            for edge in _relation_edges(diagnostic)
        ):
            records.add((page.page_id, pair.pair_id))
    return records


def _c2_conflict_cycle_records(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    for page, pair in _pairs_for_slice(annotations, "c2-conflict-cycle-safety"):
        candidate = diagnostics[ArmId.C2_ONLY][page.page_id]
        control = diagnostics[ArmId.CONTROL][page.page_id]
        reason = candidate.get("fallbackReason")
        final_order = candidate.get("finalOrder")
        fallback_order = candidate.get("fallbackOrder")
        direct_conflict = reason == "uncertain-relation-conflict"
        c2_cycle = (
            reason == "precedence-cycle"
            and control.get("fallbackReason") != "precedence-cycle"
        )
        if (
            (direct_conflict or c2_cycle)
            and final_order == fallback_order
            and candidate.get("usedPanelEvidence") is False
        ):
            records.add((page.page_id, pair.pair_id))
    return records


def _c3_accepts(diagnostic: dict[str, object]) -> bool:
    pre = diagnostic.get("preSegmentation")
    segmentation = diagnostic.get("segmentation")
    return (
        isinstance(pre, dict)
        and pre.get("reliable") is False
        and pre.get("reason") == "fewer-than-two-groups"
        and pre.get("boxCount") == 1
        and isinstance(segmentation, dict)
        and segmentation.get("reliable") is True
        and segmentation.get("reason") == "recovered-merged-frame"
        and diagnostic.get("recoveryReason")
        == "accepted-strong-anchor-plus-occlusion-supported-frame"
        and diagnostic.get("usedPanelEvidence") is True
    )


def _c3_rejects(diagnostic: dict[str, object]) -> bool:
    pre = diagnostic.get("preSegmentation")
    reason = diagnostic.get("recoveryReason")
    return (
        isinstance(pre, dict)
        and pre.get("reason") == "fewer-than-two-groups"
        and pre.get("boxCount") == 1
        and isinstance(reason, str)
        and reason.startswith("rejected-")
        and diagnostic.get("finalOrder") == diagnostic.get("fallbackOrder")
        and diagnostic.get("usedPanelEvidence") is False
    )


def _c3_records(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
    *,
    slice_name: str,
    accepted: bool,
) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    arms = (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    for page, pair in _pairs_for_slice(annotations, slice_name):
        predicate = _c3_accepts if accepted else _c3_rejects
        if any(predicate(diagnostics[arm][page.page_id]) for arm in arms):
            records.add((page.page_id, pair.pair_id))
    return records


def _orientation(value: object) -> str:
    direction = str(value or "")
    if direction in {"h", "hr"}:
        return "horizontal"
    if direction in {"v", "vr"}:
        return "vertical"
    return "ambiguous"


def _b1_pair_exercised(
    diagnostic: dict[str, object],
    pair: QualificationPair,
    mode: str,
) -> bool:
    if diagnostic.get("usedPanelEvidence") is not True:
        return False
    assignments = _assignment_by_region(diagnostic)
    first, second = assignments.get(pair.earlier), assignments.get(pair.later)
    if first is None or second is None:
        return False
    if first.get("status") != "confident" or second.get("status") != "confident":
        return False
    if first.get("assignedGroupIndex") != second.get("assignedGroupIndex"):
        return False
    directions = diagnostic.get("regionDirections")
    if not isinstance(directions, dict):
        return False
    first_orientation = _orientation(directions.get(pair.earlier))
    second_orientation = _orientation(directions.get(pair.later))
    if mode == "horizontal":
        return first_orientation == second_orientation == "horizontal"
    if mode == "vertical":
        return first_orientation == second_orientation == "vertical"
    return first_orientation != second_orientation or "ambiguous" in {
        first_orientation,
        second_orientation,
    }


def _b1_records(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
    *,
    slice_name: str,
    mode: str,
) -> set[tuple[str, str]]:
    records: set[tuple[str, str]] = set()
    for page, pair in _pairs_for_slice(annotations, slice_name):
        if (
            _b1_pair_exercised(diagnostics[ArmId.B1_ONLY][page.page_id], pair, mode)
            or _b1_pair_exercised(
                diagnostics[ArmId.C1_C2_C3_B1][page.page_id], pair, mode
            )
        ):
            records.add((page.page_id, pair.pair_id))
    return records


def build_exercise_report(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> ExerciseReport:
    counts = {
        "c1_guarded_pairs": _count(_c1_records(annotations, diagnostics)),
        "c2_gutter_pairs": _count(
            _c2_rule_records(
                annotations,
                diagnostics,
                slice_name="c2-gutter-bridge",
                rule_kind="gutter",
            )
        ),
        "c2_overlap_pairs": _count(
            _c2_rule_records(
                annotations,
                diagnostics,
                slice_name="c2-ambiguous-overlap-bridge",
                rule_kind="overlap",
            )
        ),
        "c2_pair_precedence_pairs": _count(
            _c2_rule_records(
                annotations,
                diagnostics,
                slice_name="c2-pair-precedence-slot",
                rule_kind="pair-precedence",
            )
        ),
        "c2_fail_closed_no_relation_pairs": _count(
            _c2_no_relation_records(annotations, diagnostics)
        ),
        "c2_conflict_cycle_fallback_pairs": _count(
            _c2_conflict_cycle_records(annotations, diagnostics)
        ),
        "c3_positive_pairs": _count(
            _c3_records(
                annotations,
                diagnostics,
                slice_name="c3-positive-recovery",
                accepted=True,
            )
        ),
        "c3_zero_multiple_anchor_rejection_pairs": _count(
            _c3_records(
                annotations,
                diagnostics,
                slice_name="c3-zero-multiple-anchor-negative",
                accepted=False,
            )
        ),
        "c3_zero_multiple_companion_rejection_pairs": _count(
            _c3_records(
                annotations,
                diagnostics,
                slice_name="c3-zero-multiple-companion-negative",
                accepted=False,
            )
        ),
        "c3_invalid_topology_rejection_pairs": _count(
            _c3_records(
                annotations,
                diagnostics,
                slice_name="c3-invalid-topology-negative",
                accepted=False,
            )
        ),
        "c3_insufficient_visible_support_rejection_pairs": _count(
            _c3_records(
                annotations,
                diagnostics,
                slice_name="c3-insufficient-visible-support-negative",
                accepted=False,
            )
        ),
        "b1_horizontal_pairs": _count(
            _b1_records(
                annotations,
                diagnostics,
                slice_name="b1-horizontal",
                mode="horizontal",
            )
        ),
        "b1_vertical_pairs": _count(
            _b1_records(
                annotations,
                diagnostics,
                slice_name="b1-vertical",
                mode="vertical",
            )
        ),
        "b1_mixed_pairs": _count(
            _b1_records(
                annotations,
                diagnostics,
                slice_name="b1-mixed-orientation",
                mode="mixed",
            )
        ),
    }
    return ExerciseReport(counts=counts, minima=dict(EXERCISE_MINIMA))


def exercise_minimum_met(report: ExerciseReport, name: str) -> bool:
    minimum = report.minima[name]
    return report.counts[name].count >= minimum
