"""Post-v2 Reading Order calibration candidate.

This module is diagnostic-only. It intentionally does not change production reading order
or the frozen Reading Order v2 experiment implementation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mangasensei.ocr.diagnostics.reading_order_v2 import _b0_local_order, _b1_local_order
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ExperimentRegion
from mangasensei.ocr.reading_order import (
    PanelBox,
    PanelSegmentation,
    _deterministic_topological_order,
    _line_segments,
    _panel_precedence_edges,
    _validate_boxes,
    manga_tier_order,
    segment_panel_groups,
)

# The production detector uses 3x3 Sobel and 3x3 morphology kernels. One pixel is the
# radius of that local support footprint, so a detected contour boundary is not treated as
# a confidence-bearing center-containment boundary until the center is one pixel inward.
_BOUNDARY_GUARD_PX = 1
_FRAME_MIN_SIDE_COVERAGE = 0.80
_FRAME_CLUSTER_FRACTION = 0.012
_FRAME_MIN_SEGMENT_FRACTION = 0.12
_FRAME_DEDUP_IOU = 0.80
_GUTTER_MAX_REGION_OVERLAP = 0.25


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    c1_boundary_guard: bool = False
    c2_uncertain_relations: bool = False
    c3_merged_frame_recovery: bool = False
    b1_local_order: bool = False


@dataclass(frozen=True, slots=True)
class CalibrationAssignment:
    region_id: str
    candidate_group_indices: tuple[int, ...]
    status: str
    reason: str
    assigned_group_index: int | None


@dataclass(frozen=True, slots=True)
class CalibrationRelationEdge:
    source_node: str
    target_node: str
    rule: str


@dataclass(frozen=True, slots=True)
class CalibrationDiagnostic:
    segmentation_boxes: tuple[PanelBox, ...]
    segmentation_reliable: bool
    segmentation_reason: str
    recovery_reason: str
    assignments: tuple[CalibrationAssignment, ...]
    relation_edges: tuple[CalibrationRelationEdge, ...]
    node_order: tuple[str, ...]
    fallback_reason: str | None
    used_panel_evidence: bool
    fallback_order: tuple[str, ...]
    final_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    ordered_regions: tuple[ExperimentRegion, ...]
    diagnostic: CalibrationDiagnostic


@dataclass(frozen=True, slots=True)
class _AxisCluster:
    coordinate: float
    intervals: tuple[tuple[float, float], ...]
    support: int


@dataclass(frozen=True, slots=True)
class _FrameCandidate:
    box: PanelBox
    score: float


def _region_box(region: Any) -> PanelBox:
    x1, y1, x2, y2 = (int(value) for value in region.xyxy)
    return PanelBox(x1, y1, x2, y2)


def _candidate_indices(
    boxes: Sequence[PanelBox], region: Any, *, guarded: bool
) -> tuple[int, ...]:
    x1, y1, x2, y2 = (float(value) for value in region.xyxy)
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    guard = _BOUNDARY_GUARD_PX if guarded else 0
    return tuple(
        index
        for index, box in enumerate(boxes)
        if box.x1 + guard <= center_x <= box.x2 - guard
        and box.y1 + guard <= center_y <= box.y2 - guard
    )


def _cluster_axis_lines(
    entries: Sequence[tuple[float, float, float, float]], *, tolerance: float
) -> tuple[_AxisCluster, ...]:
    groups: list[list[tuple[float, float, float, float]]] = []
    for entry in sorted(entries, key=lambda item: (item[0], item[1], item[2])):
        if groups:
            total_weight = sum(item[3] for item in groups[-1])
            mean_coordinate = (
                sum(item[0] * item[3] for item in groups[-1]) / total_weight
                if total_weight > 0
                else groups[-1][0][0]
            )
            if abs(entry[0] - mean_coordinate) <= tolerance:
                groups[-1].append(entry)
                continue
        groups.append([entry])

    result: list[_AxisCluster] = []
    for group in groups:
        total_weight = sum(item[3] for item in group)
        coordinate = sum(item[0] * item[3] for item in group) / total_weight
        intervals = tuple(sorted((min(item[1], item[2]), max(item[1], item[2])) for item in group))
        result.append(_AxisCluster(coordinate, intervals, len(group)))
    return tuple(result)


def _interval_coverage(
    intervals: Sequence[tuple[float, float]], start: float, end: float
) -> float:
    span = end - start
    if span <= 0:
        return 0.0
    clipped = sorted(
        (max(start, first), min(end, second))
        for first, second in intervals
        if min(end, second) > max(start, first)
    )
    if not clipped:
        return 0.0
    covered = 0.0
    current_start, current_end = clipped[0]
    for first, second in clipped[1:]:
        if first <= current_end:
            current_end = max(current_end, second)
        else:
            covered += current_end - current_start
            current_start, current_end = first, second
    covered += current_end - current_start
    return covered / span


def _box_iou(first: PanelBox, second: PanelBox) -> float:
    width = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
    height = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
    intersection = width * height
    union = first.area + second.area - intersection
    return intersection / union if union > 0 else 0.0


def _recover_merged_frames(
    pixels: Any, segmentation: PanelSegmentation
) -> tuple[PanelSegmentation, str]:
    if segmentation.reliable:
        return segmentation, "not-needed"
    if segmentation.reason != "fewer-than-two-groups" or len(segmentation.boxes) != 1:
        return segmentation, "not-eligible"

    import cv2

    merged = segmentation.boxes[0]
    page_height, page_width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    lines = _line_segments(gray)
    horizontal: list[tuple[float, float, float, float]] = []
    vertical: list[tuple[float, float, float, float]] = []
    min_horizontal = _FRAME_MIN_SEGMENT_FRACTION * merged.width
    min_vertical = _FRAME_MIN_SEGMENT_FRACTION * merged.height

    for x1, y1, x2, y2 in lines:
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        if not (
            merged.x1 - 3 <= center_x <= merged.x2 + 3
            and merged.y1 - 3 <= center_y <= merged.y2 + 3
        ):
            continue
        delta_x = x2 - x1
        delta_y = y2 - y1
        length = math.hypot(delta_x, delta_y)
        if abs(delta_x) > 1 and abs(delta_y) <= 0.15 * abs(delta_x) and length >= min_horizontal:
            horizontal.append((center_y, min(x1, x2), max(x1, x2), length))
        if abs(delta_y) > 1 and abs(delta_x) <= 0.15 * abs(delta_y) and length >= min_vertical:
            vertical.append((center_x, min(y1, y2), max(y1, y2), length))

    horizontal_clusters = _cluster_axis_lines(
        horizontal, tolerance=max(8.0, _FRAME_CLUSTER_FRACTION * merged.height)
    )
    vertical_clusters = _cluster_axis_lines(
        vertical, tolerance=max(8.0, _FRAME_CLUSTER_FRACTION * merged.width)
    )
    if len(horizontal_clusters) < 2 or len(vertical_clusters) < 2:
        return segmentation, "rejected-insufficient-long-frame-sides"

    candidates: list[_FrameCandidate] = []
    page_area = page_width * page_height
    for left_index, left in enumerate(vertical_clusters):
        for right in vertical_clusters[left_index + 1 :]:
            x1 = left.coordinate
            x2 = right.coordinate
            width = x2 - x1
            if width < 0.10 * page_width:
                continue
            for top_index, top in enumerate(horizontal_clusters):
                for bottom in horizontal_clusters[top_index + 1 :]:
                    y1 = top.coordinate
                    y2 = bottom.coordinate
                    height = y2 - y1
                    if height < 0.05 * page_height or width * height < 0.02 * page_area:
                        continue
                    coverages = (
                        _interval_coverage(top.intervals, x1, x2),
                        _interval_coverage(bottom.intervals, x1, x2),
                        _interval_coverage(left.intervals, y1, y2),
                        _interval_coverage(right.intervals, y1, y2),
                    )
                    if min(coverages) < _FRAME_MIN_SIDE_COVERAGE:
                        continue
                    box = PanelBox(round(x1), round(y1), round(x2), round(y2))
                    candidates.append(_FrameCandidate(box, min(coverages)))

    if len(candidates) < 2:
        return segmentation, "rejected-no-complete-strong-frames"

    selected: list[_FrameCandidate] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-item.score, item.box.y1, item.box.x1, item.box.y2, item.box.x2),
    ):
        if any(_box_iou(candidate.box, existing.box) >= _FRAME_DEDUP_IOU for existing in selected):
            continue
        selected.append(candidate)

    boxes = tuple(sorted((item.box for item in selected), key=lambda box: (box.y1, box.x1, box.y2, box.x2)))
    recovered = _validate_boxes(boxes, page_width, page_height)
    if not recovered.reliable:
        return segmentation, f"rejected-{recovered.reason}"
    return PanelSegmentation(recovered.boxes, True, "recovered-merged-frame"), "accepted-strong-four-side-frames"


def _assignment_observations(
    boxes: Sequence[PanelBox], refs: Sequence[ExperimentRegion], *, guarded: bool
) -> tuple[CalibrationAssignment, ...]:
    result: list[CalibrationAssignment] = []
    for ref in refs:
        matches = _candidate_indices(boxes, ref.region, guarded=guarded)
        if len(matches) == 1:
            result.append(
                CalibrationAssignment(
                    ref.region_id,
                    matches,
                    "confident",
                    "unique-guarded-center-containment" if guarded else "unique-center-containment",
                    matches[0],
                )
            )
        elif matches:
            result.append(
                CalibrationAssignment(
                    ref.region_id,
                    matches,
                    "ambiguous",
                    "multiple-guarded-center-containment" if guarded else "multiple-center-containment",
                    None,
                )
            )
        else:
            result.append(
                CalibrationAssignment(
                    ref.region_id,
                    (),
                    "unassigned",
                    "no-guarded-center-containment" if guarded else "no-center-containment",
                    None,
                )
            )
    return tuple(result)


def _panel_order(
    boxes: Sequence[PanelBox], groups: Sequence[Sequence[int]], fallback_ranks: Sequence[int | None]
) -> tuple[int, ...] | None:
    edges = _panel_precedence_edges(boxes)

    def tie_key(index: int) -> tuple[float, int, int, int]:
        rank_value = fallback_ranks[index]
        rank = float(rank_value) if rank_value is not None else float("inf")
        box = boxes[index]
        return (rank, box.y1, -box.x2, index)

    del groups
    return _deterministic_topological_order(
        len(boxes),
        tuple((edge.source_index, edge.target_index) for edge in edges),
        tie_key=tie_key,
    )


def _gutter_bracket(
    *,
    region_box: PanelBox,
    hard_edges: Sequence[Any],
    boxes: Sequence[PanelBox],
    groups: Sequence[Sequence[int]],
) -> tuple[int, int] | None:
    center_x = region_box.center[0]
    region_width = max(1, region_box.width)
    candidates: list[tuple[int, int]] = []
    for edge in hard_edges:
        if edge.rule != "same-level-right-before-left":
            continue
        if not groups[edge.source_index] or not groups[edge.target_index]:
            continue
        right = boxes[edge.source_index]
        left = boxes[edge.target_index]
        gap_left = left.x2
        gap_right = right.x1
        if gap_left > gap_right or not gap_left <= center_x <= gap_right:
            continue
        overlap_right = max(0, min(region_box.x2, right.x2) - max(region_box.x1, right.x1))
        overlap_left = max(0, min(region_box.x2, left.x2) - max(region_box.x1, left.x1))
        if overlap_right / region_width > _GUTTER_MAX_REGION_OVERLAP:
            continue
        if overlap_left / region_width > _GUTTER_MAX_REGION_OVERLAP:
            continue
        candidates.append((edge.source_index, edge.target_index))
    return candidates[0] if len(candidates) == 1 else None


def _uncertain_relations(
    *,
    uncertain_region_index: int,
    raw_regions: Sequence[Any],
    boxes: Sequence[PanelBox],
    groups: Sequence[Sequence[int]],
    hard_edges: Sequence[Any],
    active_panel_order: Sequence[int],
) -> tuple[tuple[tuple[int, int, str], ...], str]:
    region_box = _region_box(raw_regions[uncertain_region_index])
    panel_count = len(boxes)
    uncertain_node = panel_count
    bracket = _gutter_bracket(
        region_box=region_box,
        hard_edges=hard_edges,
        boxes=boxes,
        groups=groups,
    )
    proposed: list[tuple[int, int, str]] = []
    if bracket is not None:
        right, left = bracket
        proposed.extend(
            (
                (right, uncertain_node, "unique-gutter-between-hard-panels"),
                (uncertain_node, left, "unique-gutter-between-hard-panels"),
            )
        )
    else:
        for panel_index, panel_box in enumerate(boxes):
            if not groups[panel_index]:
                continue
            pair_edges = _panel_precedence_edges((panel_box, region_box))
            if len(pair_edges) != 1:
                continue
            edge = pair_edges[0]
            if edge.source_index == 0:
                proposed.append((panel_index, uncertain_node, f"uncertain-{edge.rule}"))
            else:
                proposed.append((uncertain_node, panel_index, f"uncertain-{edge.rule}"))

    positions = {panel_index: position for position, panel_index in enumerate(active_panel_order)}
    predecessors = [positions[source] for source, target, _ in proposed if target == uncertain_node and source in positions]
    successors = [positions[target] for source, target, _ in proposed if source == uncertain_node and target in positions]
    if not predecessors or not successors:
        return (), "rejected-one-sided-or-insufficient-relations"
    latest_predecessor = max(predecessors)
    earliest_successor = min(successors)
    if latest_predecessor >= earliest_successor:
        return (), "conflict"
    if latest_predecessor + 1 != earliest_successor:
        return (), "rejected-non-unique-slot"
    return tuple(proposed), "accepted-unique-slot"


def run_post_v2_calibration_candidate(
    pixels: Any,
    regions: Sequence[ExperimentRegion],
    *,
    page_height: int,
    config: CalibrationConfig,
) -> CalibrationResult:
    """Run one calibration-only post-v2 candidate without consulting ground truth."""
    refs = tuple(regions)
    raw_regions = tuple(ref.region for ref in refs)
    refs_by_object = {id(ref.region): ref for ref in refs}
    if len(refs_by_object) != len(refs):
        raise ValueError("one runtime region object cannot back multiple experiment regions")

    fallback_raw = tuple(manga_tier_order(raw_regions, page_height=page_height))
    fallback_order = tuple(refs_by_object[id(region)].region_id for region in fallback_raw)
    if len(raw_regions) < 2:
        diagnostic = CalibrationDiagnostic(
            (), False, "not-run", "not-attempted", (), (), (), "fewer-than-two-regions", False,
            fallback_order, fallback_order,
        )
        return CalibrationResult(tuple(refs_by_object[id(region)] for region in fallback_raw), diagnostic)

    segmentation = segment_panel_groups(pixels)
    recovery_reason = "disabled"
    if config.c3_merged_frame_recovery:
        segmentation, recovery_reason = _recover_merged_frames(pixels, segmentation)
    if not segmentation.reliable:
        diagnostic = CalibrationDiagnostic(
            segmentation.boxes,
            False,
            segmentation.reason,
            recovery_reason,
            (),
            (),
            (),
            segmentation.reason,
            False,
            fallback_order,
            fallback_order,
        )
        return CalibrationResult(tuple(refs_by_object[id(region)] for region in fallback_raw), diagnostic)

    assignments = _assignment_observations(
        segmentation.boxes, refs, guarded=config.c1_boundary_guard
    )
    groups_mutable: list[list[int]] = [[] for _ in segmentation.boxes]
    uncertain: list[int] = []
    for region_index, assignment in enumerate(assignments):
        if assignment.assigned_group_index is None:
            uncertain.append(region_index)
        else:
            groups_mutable[assignment.assigned_group_index].append(region_index)
    groups = tuple(tuple(group) for group in groups_mutable)
    if sum(bool(group) for group in groups) < 2:
        diagnostic = CalibrationDiagnostic(
            segmentation.boxes,
            True,
            segmentation.reason,
            recovery_reason,
            assignments,
            (),
            (),
            "insufficient-confident-panel-groups",
            False,
            fallback_order,
            fallback_order,
        )
        return CalibrationResult(tuple(refs_by_object[id(region)] for region in fallback_raw), diagnostic)

    fallback_position = {id(region): index for index, region in enumerate(fallback_raw)}
    panel_ranks = tuple(
        min((fallback_position[id(raw_regions[index])] for index in group), default=None)
        for group in groups
    )
    hard_edges = _panel_precedence_edges(segmentation.boxes)
    hard_panel_order = _panel_order(segmentation.boxes, groups, panel_ranks)
    if hard_panel_order is None:
        diagnostic = CalibrationDiagnostic(
            segmentation.boxes,
            True,
            segmentation.reason,
            recovery_reason,
            assignments,
            (),
            (),
            "precedence-cycle",
            False,
            fallback_order,
            fallback_order,
        )
        return CalibrationResult(tuple(refs_by_object[id(region)] for region in fallback_raw), diagnostic)

    panel_count = len(segmentation.boxes)
    uncertain_by_node = {
        panel_count + offset: region_index for offset, region_index in enumerate(uncertain)
    }
    graph_edges: list[tuple[int, int]] = [
        (edge.source_index, edge.target_index) for edge in hard_edges
    ]
    relation_diagnostics: list[CalibrationRelationEdge] = []
    active_panel_order = tuple(index for index in hard_panel_order if groups[index])

    if config.c2_uncertain_relations:
        for offset, region_index in enumerate(uncertain):
            node_index = panel_count + offset
            proposed, relation_status = _uncertain_relations(
                uncertain_region_index=region_index,
                raw_regions=raw_regions,
                boxes=segmentation.boxes,
                groups=groups,
                hard_edges=hard_edges,
                active_panel_order=active_panel_order,
            )
            if relation_status == "conflict":
                diagnostic = CalibrationDiagnostic(
                    segmentation.boxes,
                    True,
                    segmentation.reason,
                    recovery_reason,
                    assignments,
                    tuple(relation_diagnostics),
                    (),
                    "uncertain-relation-conflict",
                    False,
                    fallback_order,
                    fallback_order,
                )
                return CalibrationResult(tuple(refs_by_object[id(region)] for region in fallback_raw), diagnostic)
            for source, target, rule in proposed:
                mapped_source = node_index if source == panel_count else source
                mapped_target = node_index if target == panel_count else target
                graph_edges.append((mapped_source, mapped_target))
                relation_diagnostics.append(
                    CalibrationRelationEdge(
                        f"u{region_index:03d}" if mapped_source == node_index else f"g{mapped_source:03d}",
                        f"u{region_index:03d}" if mapped_target == node_index else f"g{mapped_target:03d}",
                        rule,
                    )
                )

    def tie_key(node_index: int) -> tuple[float, int, int, int, int]:
        if node_index < panel_count:
            rank_value = panel_ranks[node_index]
            rank = float(rank_value) if rank_value is not None else float("inf")
            box = segmentation.boxes[node_index]
            return (rank, 0, box.y1, -box.x2, node_index)
        region_index = uncertain_by_node[node_index]
        raw = raw_regions[region_index]
        box = _region_box(raw)
        return (
            float(fallback_position[id(raw)]),
            1,
            box.y1,
            -box.x2,
            region_index,
        )

    node_order = _deterministic_topological_order(
        panel_count + len(uncertain), tuple(graph_edges), tie_key=tie_key
    )
    if node_order is None:
        diagnostic = CalibrationDiagnostic(
            segmentation.boxes,
            True,
            segmentation.reason,
            recovery_reason,
            assignments,
            tuple(relation_diagnostics),
            (),
            "precedence-cycle",
            False,
            fallback_order,
            fallback_order,
        )
        return CalibrationResult(tuple(refs_by_object[id(region)] for region in fallback_raw), diagnostic)

    ordered_raw: list[Any] = []
    node_labels: list[str] = []
    for node_index in node_order:
        if node_index < panel_count:
            group_regions = [raw_regions[index] for index in groups[node_index]]
            if config.b1_local_order:
                local, _ = _b1_local_order(
                    group_regions,
                    refs_by_object,
                    page_height=page_height,
                    tier_prefix=f"g{node_index:03d}-",
                )
            else:
                local, _ = _b0_local_order(
                    group_regions,
                    refs_by_object,
                    page_height=page_height,
                    tier_prefix=f"g{node_index:03d}-",
                )
            ordered_raw.extend(local)
            node_labels.append(f"g{node_index:03d}")
        else:
            region_index = uncertain_by_node[node_index]
            ordered_raw.append(raw_regions[region_index])
            node_labels.append(f"u{region_index:03d}")

    if len(ordered_raw) != len(raw_regions) or {id(region) for region in ordered_raw} != {
        id(region) for region in raw_regions
    }:
        raise AssertionError("calibration candidate changed the OCR region set")
    ordered_refs = tuple(refs_by_object[id(region)] for region in ordered_raw)
    final_order = tuple(ref.region_id for ref in ordered_refs)
    diagnostic = CalibrationDiagnostic(
        segmentation.boxes,
        True,
        segmentation.reason,
        recovery_reason,
        assignments,
        tuple(relation_diagnostics),
        tuple(node_labels),
        None,
        True,
        fallback_order,
        final_order,
    )
    return CalibrationResult(ordered_refs, diagnostic)
