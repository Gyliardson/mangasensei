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
    "c3_rejection_pages": 8,
    "b1_horizontal_pairs": 4,
    "b1_vertical_pairs": 4,
    "b1_mixed_pairs": 4,
}


class InvalidDiagnosticError(ValueError):
    """V3 harness-invalid diagnostic boundary failure.

    A future executable v3 runner must classify this as INVALID_EXPERIMENT / harness-invalid.
    The methodology evaluator intentionally raises before delegating to the consumed v2
    evaluator so malformed evidence can never satisfy a reachability predicate.
    """


def _count_pages(page_ids: set[str]) -> ExerciseCount:
    ordered = tuple(sorted(page_ids))
    return ExerciseCount(len(ordered), (), ordered)


def _require(condition: bool, where: str) -> None:
    if not condition:
        raise InvalidDiagnosticError(where)


def _string_list(value: object, where: str) -> list[str]:
    _require(isinstance(value, list), f"{where}: list required")
    assert isinstance(value, list)
    _require(all(isinstance(item, str) for item in value), f"{where}: string list required")
    return [str(item) for item in value]


def _validate_diagnostic(
    diagnostic: object,
    *,
    page: PageGroundTruth,
    arm: ArmId,
) -> None:
    where = f"diagnostics[{arm.value}][{page.page_id}]"
    _require(isinstance(diagnostic, dict), f"{where}: object required")
    assert isinstance(diagnostic, dict)

    required = {
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
    missing = required - set(diagnostic)
    _require(not missing, f"{where}: missing required fields {sorted(missing)}")

    pre = diagnostic["preSegmentation"]
    _require(isinstance(pre, dict), f"{where}.preSegmentation: object required")
    assert isinstance(pre, dict)
    _require(
        isinstance(pre.get("reliable"), bool),
        f"{where}.preSegmentation.reliable: bool required",
    )
    _require(isinstance(pre.get("reason"), str), f"{where}.preSegmentation.reason: string required")
    box_count = pre.get("boxCount")
    _require(
        isinstance(box_count, int) and not isinstance(box_count, bool) and box_count >= 0,
        f"{where}.preSegmentation.boxCount: non-negative int required",
    )

    segmentation = diagnostic["segmentation"]
    _require(isinstance(segmentation, dict), f"{where}.segmentation: object required")
    assert isinstance(segmentation, dict)
    _require(
        isinstance(segmentation.get("reliable"), bool),
        f"{where}.segmentation.reliable: bool required",
    )
    _require(
        isinstance(segmentation.get("reason"), str),
        f"{where}.segmentation.reason: string required",
    )

    recovery_reason = diagnostic["recoveryReason"]
    _require(
        recovery_reason is None or isinstance(recovery_reason, str),
        f"{where}.recoveryReason: string or null required",
    )
    fallback_reason = diagnostic["fallbackReason"]
    _require(
        fallback_reason is None or isinstance(fallback_reason, str),
        f"{where}.fallbackReason: string or null required",
    )
    _require(
        isinstance(diagnostic["usedPanelEvidence"], bool),
        f"{where}.usedPanelEvidence: bool required",
    )

    expected_regions = set(page.reading_order) | set(page.unscored_region_ids)
    final_order = _string_list(diagnostic["finalOrder"], f"{where}.finalOrder")
    fallback_order = _string_list(diagnostic["fallbackOrder"], f"{where}.fallbackOrder")
    for name, order in (("finalOrder", final_order), ("fallbackOrder", fallback_order)):
        _require(len(order) == len(set(order)), f"{where}.{name}: duplicate region IDs")
        _require(set(order) == expected_regions, f"{where}.{name}: region coverage mismatch")
    if diagnostic["usedPanelEvidence"] is False:
        _require(final_order == fallback_order, f"{where}: fallback evidence/order inconsistent")

    assignments = diagnostic["assignments"]
    _require(isinstance(assignments, list), f"{where}.assignments: list required")
    assert isinstance(assignments, list)
    seen_regions: set[str] = set()
    for index, item in enumerate(assignments):
        item_where = f"{where}.assignments[{index}]"
        _require(isinstance(item, dict), f"{item_where}: object required")
        assert isinstance(item, dict)
        for field in (
            "regionId",
            "candidateGroupIndices",
            "status",
            "assignedGroupIndex",
            "uncertainNodeLabel",
        ):
            _require(field in item, f"{item_where}.{field}: required")
        region_id = item["regionId"]
        _require(
            isinstance(region_id, str) and region_id in expected_regions,
            f"{item_where}.regionId: invalid",
        )
        assert isinstance(region_id, str)
        _require(region_id not in seen_regions, f"{item_where}.regionId: duplicate")
        seen_regions.add(region_id)
        candidates = item["candidateGroupIndices"]
        _require(
            isinstance(candidates, list)
            and all(isinstance(value, int) and not isinstance(value, bool) for value in candidates),
            f"{item_where}.candidateGroupIndices: int list required",
        )
        _require(
            item["status"] in {"confident", "ambiguous", "unassigned"},
            f"{item_where}.status: invalid",
        )
        assigned = item["assignedGroupIndex"]
        _require(
            assigned is None or (isinstance(assigned, int) and not isinstance(assigned, bool)),
            f"{item_where}.assignedGroupIndex: int or null required",
        )
        label = item["uncertainNodeLabel"]
        _require(
            label is None or isinstance(label, str),
            f"{item_where}.uncertainNodeLabel: string or null required",
        )
        _require(
            (assigned is None and isinstance(label, str))
            or (assigned is not None and label is None),
            f"{item_where}: assignedGroupIndex/uncertainNodeLabel inconsistent",
        )

    edges = diagnostic["relationEdges"]
    _require(isinstance(edges, list), f"{where}.relationEdges: list required")
    assert isinstance(edges, list)
    for index, edge in enumerate(edges):
        edge_where = f"{where}.relationEdges[{index}]"
        _require(isinstance(edge, dict), f"{edge_where}: object required")
        assert isinstance(edge, dict)
        _require(
            all(isinstance(edge.get(field), str) for field in ("sourceNode", "targetNode", "rule")),
            f"{edge_where}: sourceNode/targetNode/rule strings required",
        )

    directions = diagnostic["regionDirections"]
    _require(isinstance(directions, dict), f"{where}.regionDirections: object required")
    assert isinstance(directions, dict)
    _require(
        set(directions) == expected_regions
        and all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in directions.items()
        ),
        f"{where}.regionDirections: exact string region coverage required",
    )


def _required_arms(page: PageGroundTruth) -> set[ArmId]:
    slices = {slice_name for pair in page.qualification_pairs for slice_name in pair.slices}
    arms: set[ArmId] = set()
    if "c1-boundary-positive" in slices:
        arms.update({ArmId.CONTROL, ArmId.C1_ONLY})
    if slices & {
        "c2-gutter-bridge",
        "c2-ambiguous-overlap-bridge",
        "c2-pair-precedence-slot",
    }:
        arms.update({ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1})
    if "c2-one-sided-non-unique-fail-closed" in slices:
        arms.add(ArmId.C2_ONLY)
    if "c2-conflict-cycle-safety" in slices:
        arms.update({ArmId.CONTROL, ArmId.C2_ONLY})
    if "c3-positive-recovery" in slices or slices & C3_NEGATIVE_SLICES:
        arms.update({ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1})
    if slices & {"b1-horizontal", "b1-vertical", "b1-mixed-orientation"}:
        arms.update({ArmId.B1_ONLY, ArmId.C1_C2_C3_B1})
    return arms


def _validate_boundary(
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> None:
    _require(isinstance(diagnostics, dict), "diagnostics: object required")
    for page in annotations:
        for arm in _required_arms(page):
            _require(arm in diagnostics, f"diagnostics: missing arm {arm.value}")
            arm_pages = diagnostics[arm]
            _require(isinstance(arm_pages, dict), f"diagnostics[{arm.value}]: object required")
            _require(
                page.page_id in arm_pages,
                f"diagnostics[{arm.value}]: missing page {page.page_id}",
            )
            _validate_diagnostic(arm_pages[page.page_id], page=page, arm=arm)


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
    records: set[str] = set()
    arms = (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    for page in annotations:
        if not any(
            C3_NEGATIVE_SLICES.intersection(pair.slices) for pair in page.qualification_pairs
        ):
            continue
        if any(_c3_generic_rejects(diagnostics[arm][page.page_id]) for arm in arms):
            records.add(page.page_id)
    return records


def build_exercise_report_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> ExerciseReport:
    """Build the frozen v3 reachability report without changing consumed v2 semantics.

    V3 first validates every diagnostic arm/page that the delegated v2 predicates may
    inspect. Missing, malformed, or internally inconsistent evidence raises
    InvalidDiagnosticError and is frozen as INVALID_EXPERIMENT / harness-invalid for a
    future runner. Only validated diagnostics are delegated to the unchanged v2
    evaluator. Generic C3 rejection is intentionally page-level because the candidate
    exposes only a page-level rejection witness; one page can therefore contribute at
    most one rejection exercise regardless of how many eligible pairs it contains.
    """

    _validate_boundary(annotations, diagnostics)
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
