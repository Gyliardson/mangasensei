"""Calibration-only Reading Order candidate after the frozen v2 experiment."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

from mangasensei.ocr.diagnostics.reading_order_v2 import _b0_local_order, _b1_local_order
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ExperimentRegion
from mangasensei.ocr.reading_order import (
    _MAX_AMBIGUOUS_OVERLAP,
    PanelBox,
    PanelSegmentation,
    _deterministic_topological_order,
    _line_segments,
    _panel_precedence_edges,
    _PanelPrecedenceEdge,
    _validate_boxes,
    manga_tier_order,
    segment_panel_groups,
)

# The detector uses 3x3 Sobel/morphology support. One pixel is therefore the local
# boundary uncertainty radius for confidence-bearing center containment.
_BOUNDARY_GUARD_PX = 1
_FRAME_MIN_SIDE_COVERAGE = 0.80
_FRAME_CLUSTER_FRACTION = 0.012
_FRAME_MIN_SEGMENT_FRACTION = 0.12
_FRAME_DEDUP_IOU = 0.80
_GUTTER_MAX_REGION_OVERLAP = 0.25
_SAME_LEVEL_MIN_Y_OVERLAP = 0.25


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


@dataclass(frozen=True, slots=True)
class _FrameCandidate:
    box: PanelBox
    score: float


@dataclass(frozen=True, slots=True)
class _FrameHypothesis:
    box: PanelBox
    coverages: tuple[float, float, float, float]
    top: _AxisCluster
    bottom: _AxisCluster
    left: _AxisCluster
    right: _AxisCluster


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
            mean = sum(item[0] * item[3] for item in groups[-1]) / total_weight
            if abs(entry[0] - mean) <= tolerance:
                groups[-1].append(entry)
                continue
        groups.append([entry])

    result: list[_AxisCluster] = []
    for group in groups:
        total_weight = sum(item[3] for item in group)
        coordinate = sum(item[0] * item[3] for item in group) / total_weight
        intervals = tuple(
            sorted((min(item[1], item[2]), max(item[1], item[2])) for item in group)
        )
        result.append(_AxisCluster(coordinate, intervals))
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
    return (covered + current_end - current_start) / span


def _box_iou(first: PanelBox, second: PanelBox) -> float:
    width = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
    height = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
    intersection = width * height
    union = first.area + second.area - intersection
    return intersection / union if union > 0 else 0.0


def _overlap_ratio(first: PanelBox, second: PanelBox, *, axis: str) -> float:
    if axis == "x":
        overlap = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
        denominator = min(first.width, second.width)
    else:
        overlap = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
        denominator = min(first.height, second.height)
    return overlap / denominator if denominator > 0 else 0.0


def _dedupe_frame_candidates(
    candidates: Sequence[_FrameCandidate],
) -> tuple[_FrameCandidate, ...]:
    selected: list[_FrameCandidate] = []
    ordered = sorted(
        candidates,
        key=lambda item: (
            -item.score,
            item.box.y1,
            item.box.x1,
            item.box.y2,
            item.box.x2,
        ),
    )
    for candidate in ordered:
        if any(
            _box_iou(candidate.box, existing.box) >= _FRAME_DEDUP_IOU
            for existing in selected
        ):
            continue
        selected.append(candidate)
    return tuple(selected)


def _visible_segments(
    start: float,
    end: float,
    masks: Sequence[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    if end <= start:
        return ()
    clipped = sorted(
        (max(start, first), min(end, second))
        for first, second in masks
        if min(end, second) > max(start, first)
    )
    merged: list[tuple[float, float]] = []
    for first, second in clipped:
        if not merged or first > merged[-1][1]:
            merged.append((first, second))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], second))

    visible: list[tuple[float, float]] = []
    cursor = start
    for first, second in merged:
        if first > cursor:
            visible.append((cursor, first))
        cursor = max(cursor, second)
    if cursor < end:
        visible.append((cursor, end))
    return tuple(visible)


def _coverage_over_segments(
    intervals: Sequence[tuple[float, float]],
    segments: Sequence[tuple[float, float]],
) -> float:
    total = sum(end - start for start, end in segments)
    if total <= 0:
        return 0.0
    covered = 0.0
    for start, end in segments:
        covered += _interval_coverage(intervals, start, end) * (end - start)
    return covered / total


def _side_occlusion_masks(
    box: PanelBox,
    anchor: PanelBox,
    *,
    side: str,
) -> tuple[tuple[float, float], ...]:
    if side == "top":
        coordinate = box.y1
        if anchor.y1 < coordinate < anchor.y2:
            return ((max(box.x1, anchor.x1), min(box.x2, anchor.x2)),)
    elif side == "bottom":
        coordinate = box.y2
        if anchor.y1 < coordinate < anchor.y2:
            return ((max(box.x1, anchor.x1), min(box.x2, anchor.x2)),)
    elif side == "left":
        coordinate = box.x1
        if anchor.x1 < coordinate < anchor.x2:
            return ((max(box.y1, anchor.y1), min(box.y2, anchor.y2)),)
    elif side == "right":
        coordinate = box.x2
        if anchor.x1 < coordinate < anchor.x2:
            return ((max(box.y1, anchor.y1), min(box.y2, anchor.y2)),)
    else:
        raise ValueError(f"unknown frame side: {side}")
    return ()


def _visible_side_coverages(
    hypothesis: _FrameHypothesis,
    box: PanelBox,
    anchor: PanelBox,
    merged: PanelBox,
) -> tuple[float, float, float, float] | None:
    sides = (
        ("top", hypothesis.top.intervals, float(box.x1), float(box.x2)),
        ("bottom", hypothesis.bottom.intervals, float(box.x1), float(box.x2)),
        ("left", hypothesis.left.intervals, float(box.y1), float(box.y2)),
        ("right", hypothesis.right.intervals, float(box.y1), float(box.y2)),
    )
    coverages: list[float] = []
    for side, intervals, start, end in sides:
        visible = _visible_segments(
            start,
            end,
            _side_occlusion_masks(box, anchor, side=side),
        )
        visible_length = sum(second - first for first, second in visible)
        required_length = _FRAME_MIN_SEGMENT_FRACTION * (
            merged.width if side in {"top", "bottom"} else merged.height
        )
        if visible_length < required_length:
            return None
        coverages.append(_coverage_over_segments(intervals, visible))
    return tuple(coverages)  # type: ignore[return-value]


def _has_adjacent_strong_corner(coverages: Sequence[float]) -> bool:
    strong = tuple(value >= _FRAME_MIN_SIDE_COVERAGE for value in coverages)
    return any(
        strong[first] and strong[second]
        for first, second in ((0, 2), (0, 3), (1, 2), (1, 3))
    )


def _endpoint_estimate(
    clusters: Sequence[_AxisCluster], *, use_end: bool
) -> float | None:
    points = [
        interval[1 if use_end else 0]
        for cluster in clusters
        for interval in cluster.intervals
    ]
    return float(median(points)) if points else None


def _refine_occluded_box(
    hypothesis: _FrameHypothesis,
    merged: PanelBox,
) -> PanelBox | None:
    top_strong, bottom_strong, left_strong, right_strong = (
        value >= _FRAME_MIN_SIDE_COVERAGE for value in hypothesis.coverages
    )
    x_tolerance = max(8.0, _FRAME_CLUSTER_FRACTION * merged.width)
    y_tolerance = max(8.0, _FRAME_CLUSTER_FRACTION * merged.height)
    x1 = float(hypothesis.box.x1)
    x2 = float(hypothesis.box.x2)
    y1 = float(hypothesis.box.y1)
    y2 = float(hypothesis.box.y2)

    if not left_strong:
        estimate = _endpoint_estimate(
            tuple(
                cluster
                for cluster, is_strong in (
                    (hypothesis.top, top_strong),
                    (hypothesis.bottom, bottom_strong),
                )
                if is_strong
            ),
            use_end=False,
        )
        if estimate is None or abs(estimate - x1) > x_tolerance:
            return None
        x1 = estimate
    if not right_strong:
        estimate = _endpoint_estimate(
            tuple(
                cluster
                for cluster, is_strong in (
                    (hypothesis.top, top_strong),
                    (hypothesis.bottom, bottom_strong),
                )
                if is_strong
            ),
            use_end=True,
        )
        if estimate is None or abs(estimate - x2) > x_tolerance:
            return None
        x2 = estimate
    if not top_strong:
        estimate = _endpoint_estimate(
            tuple(
                cluster
                for cluster, is_strong in (
                    (hypothesis.left, left_strong),
                    (hypothesis.right, right_strong),
                )
                if is_strong
            ),
            use_end=False,
        )
        if estimate is None or abs(estimate - y1) > y_tolerance:
            return None
        y1 = estimate
    if not bottom_strong:
        estimate = _endpoint_estimate(
            tuple(
                cluster
                for cluster, is_strong in (
                    (hypothesis.left, left_strong),
                    (hypothesis.right, right_strong),
                )
                if is_strong
            ),
            use_end=True,
        )
        if estimate is None or abs(estimate - y2) > y_tolerance:
            return None
        y2 = estimate

    refined = PanelBox(round(x1), round(y1), round(x2), round(y2))
    if refined.width <= 0 or refined.height <= 0:
        return None
    return refined


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
    horizontal: list[tuple[float, float, float, float]] = []
    vertical: list[tuple[float, float, float, float]] = []
    min_horizontal = _FRAME_MIN_SEGMENT_FRACTION * merged.width
    min_vertical = _FRAME_MIN_SEGMENT_FRACTION * merged.height

    for x1, y1, x2, y2 in _line_segments(gray):
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
        if (
            abs(delta_x) > 1
            and abs(delta_y) <= 0.15 * abs(delta_x)
            and length >= min_horizontal
        ):
            horizontal.append((center_y, min(x1, x2), max(x1, x2), length))
        if (
            abs(delta_y) > 1
            and abs(delta_x) <= 0.15 * abs(delta_y)
            and length >= min_vertical
        ):
            vertical.append((center_x, min(y1, y2), max(y1, y2), length))

    horizontal_clusters = _cluster_axis_lines(
        horizontal,
        tolerance=max(8.0, _FRAME_CLUSTER_FRACTION * merged.height),
    )
    vertical_clusters = _cluster_axis_lines(
        vertical,
        tolerance=max(8.0, _FRAME_CLUSTER_FRACTION * merged.width),
    )
    if len(horizontal_clusters) < 2 or len(vertical_clusters) < 2:
        return segmentation, "rejected-insufficient-long-frame-sides"

    hypotheses: list[_FrameHypothesis] = []
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
                    hypotheses.append(
                        _FrameHypothesis(
                            PanelBox(round(x1), round(y1), round(x2), round(y2)),
                            coverages,
                            top,
                            bottom,
                            left,
                            right,
                        )
                    )

    strong = _dedupe_frame_candidates(
        tuple(
            _FrameCandidate(hypothesis.box, min(hypothesis.coverages))
            for hypothesis in hypotheses
            if min(hypothesis.coverages) >= _FRAME_MIN_SIDE_COVERAGE
        )
    )
    if len(strong) >= 2:
        boxes = tuple(
            sorted(
                (candidate.box for candidate in strong),
                key=lambda box: (box.y1, box.x1, box.y2, box.x2),
            )
        )
        recovered = _validate_boxes(boxes, page_width, page_height)
        if not recovered.reliable:
            return segmentation, f"rejected-{recovered.reason}"
        accepted = PanelSegmentation(recovered.boxes, True, "recovered-merged-frame")
        return accepted, "accepted-multiple-strong-four-side-frames"

    if len(strong) != 1:
        return segmentation, "rejected-no-unique-strong-frame-anchor"

    anchor = strong[0].box
    occlusion_supported: list[_FrameCandidate] = []
    for hypothesis in hypotheses:
        if min(hypothesis.coverages) >= _FRAME_MIN_SIDE_COVERAGE:
            continue
        if not _has_adjacent_strong_corner(hypothesis.coverages):
            continue
        refined = _refine_occluded_box(hypothesis, merged)
        if refined is None:
            continue
        visible_coverages = _visible_side_coverages(
            hypothesis,
            refined,
            anchor,
            merged,
        )
        if visible_coverages is None:
            continue
        if min(visible_coverages) < _FRAME_MIN_SIDE_COVERAGE:
            continue
        occlusion_supported.append(_FrameCandidate(refined, min(visible_coverages)))

    companions = tuple(
        candidate
        for candidate in _dedupe_frame_candidates(occlusion_supported)
        if _box_iou(candidate.box, anchor) < _FRAME_DEDUP_IOU
    )
    if len(companions) != 1:
        return segmentation, "rejected-ambiguous-or-missing-occlusion-supported-frame"

    boxes = tuple(
        sorted(
            (anchor, companions[0].box),
            key=lambda box: (box.y1, box.x1, box.y2, box.x2),
        )
    )
    recovered = _validate_boxes(boxes, page_width, page_height)
    if not recovered.reliable:
        return segmentation, f"rejected-{recovered.reason}"
    accepted = PanelSegmentation(recovered.boxes, True, "recovered-merged-frame")
    return accepted, "accepted-strong-anchor-plus-occlusion-supported-frame"


def _assignment_observations(
    boxes: Sequence[PanelBox],
    refs: Sequence[ExperimentRegion],
    *,
    guarded: bool,
) -> tuple[CalibrationAssignment, ...]:
    result: list[CalibrationAssignment] = []
    for ref in refs:
        matches = _candidate_indices(boxes, ref.region, guarded=guarded)
        if len(matches) == 1:
            reason = (
                "unique-guarded-center-containment"
                if guarded
                else "unique-center-containment"
            )
            result.append(
                CalibrationAssignment(ref.region_id, matches, "confident", reason, matches[0])
            )
        elif matches:
            reason = (
                "multiple-guarded-center-containment"
                if guarded
                else "multiple-center-containment"
            )
            result.append(
                CalibrationAssignment(ref.region_id, matches, "ambiguous", reason, None)
            )
        else:
            reason = (
                "no-guarded-center-containment" if guarded else "no-center-containment"
            )
            result.append(CalibrationAssignment(ref.region_id, (), "unassigned", reason, None))
    return tuple(result)


def _hard_panel_order(
    boxes: Sequence[PanelBox], fallback_ranks: Sequence[int | None]
) -> tuple[int, ...] | None:
    edges = _panel_precedence_edges(boxes)

    def tie_key(index: int) -> tuple[float, int, int, int]:
        rank_value = fallback_ranks[index]
        rank = float(rank_value) if rank_value is not None else float("inf")
        box = boxes[index]
        return (rank, box.y1, -box.x2, index)

    return _deterministic_topological_order(
        len(boxes),
        tuple((edge.source_index, edge.target_index) for edge in edges),
        tie_key=tie_key,
    )


def _unique_gutter_bracket(
    region_box: PanelBox,
    hard_edges: Sequence[_PanelPrecedenceEdge],
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
        right_overlap = max(
            0,
            min(region_box.x2, right.x2) - max(region_box.x1, right.x1),
        )
        left_overlap = max(
            0,
            min(region_box.x2, left.x2) - max(region_box.x1, left.x1),
        )
        if right_overlap / region_width > _GUTTER_MAX_REGION_OVERLAP:
            continue
        if left_overlap / region_width > _GUTTER_MAX_REGION_OVERLAP:
            continue
        candidates.append((edge.source_index, edge.target_index))
    return candidates[0] if len(candidates) == 1 else None


def _ambiguous_overlap_bridge(
    assignment: CalibrationAssignment,
    boxes: Sequence[PanelBox],
    groups: Sequence[Sequence[int]],
    active_panel_order: Sequence[int],
) -> tuple[int, int] | None:
    if assignment.status != "ambiguous" or len(assignment.candidate_group_indices) != 2:
        return None
    first_index, second_index = assignment.candidate_group_indices
    if not groups[first_index] or not groups[second_index]:
        return None
    first = boxes[first_index]
    second = boxes[second_index]
    x_overlap = _overlap_ratio(first, second, axis="x")
    y_overlap = _overlap_ratio(first, second, axis="y")
    if not 0 < x_overlap <= _MAX_AMBIGUOUS_OVERLAP:
        return None
    if y_overlap < _SAME_LEVEL_MIN_Y_OVERLAP:
        return None
    if first.center[0] == second.center[0]:
        return None
    right_index, left_index = (
        (first_index, second_index)
        if first.center[0] > second.center[0]
        else (second_index, first_index)
    )
    positions = {panel: position for position, panel in enumerate(active_panel_order)}
    if right_index not in positions or left_index not in positions:
        return None
    if positions[left_index] != positions[right_index] + 1:
        return None
    return right_index, left_index


def _uncertain_relation_edges(
    *,
    region_index: int,
    assignment: CalibrationAssignment,
    raw_regions: Sequence[Any],
    boxes: Sequence[PanelBox],
    groups: Sequence[Sequence[int]],
    hard_edges: Sequence[_PanelPrecedenceEdge],
    active_panel_order: Sequence[int],
    uncertain_node: int,
) -> tuple[tuple[tuple[int, int, str], ...], str]:
    region_box = _region_box(raw_regions[region_index])
    gutter = _unique_gutter_bracket(region_box, hard_edges, boxes, groups)
    if gutter is not None:
        right, left = gutter
        rule = "unique-gutter-between-hard-panels"
        return ((right, uncertain_node, rule), (uncertain_node, left, rule)), "accepted"

    bridge = _ambiguous_overlap_bridge(assignment, boxes, groups, active_panel_order)
    if bridge is not None:
        right, left = bridge
        rule = "validated-overlap-bridge-right-before-left"
        return ((right, uncertain_node, rule), (uncertain_node, left, rule)), "accepted"

    proposed: list[tuple[int, int, str]] = []
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

    positions = {panel: position for position, panel in enumerate(active_panel_order)}
    predecessors = [
        positions[source]
        for source, target, _ in proposed
        if target == uncertain_node and source in positions
    ]
    successors = [
        positions[target]
        for source, target, _ in proposed
        if source == uncertain_node and target in positions
    ]
    if not predecessors or not successors:
        return (), "rejected-insufficient-two-sided-relations"
    latest_predecessor = max(predecessors)
    earliest_successor = min(successors)
    if latest_predecessor >= earliest_successor:
        return (), "conflict"
    if latest_predecessor + 1 != earliest_successor:
        return (), "rejected-non-unique-slot"
    return tuple(proposed), "accepted"


def _fallback_result(
    refs_by_object: dict[int, ExperimentRegion],
    fallback_raw: Sequence[Any],
    *,
    segmentation: PanelSegmentation | None,
    recovery_reason: str,
    assignments: tuple[CalibrationAssignment, ...] = (),
    relation_edges: tuple[CalibrationRelationEdge, ...] = (),
    fallback_reason: str,
) -> CalibrationResult:
    fallback_refs = tuple(refs_by_object[id(region)] for region in fallback_raw)
    fallback_order = tuple(ref.region_id for ref in fallback_refs)
    diagnostic = CalibrationDiagnostic(
        segmentation_boxes=segmentation.boxes if segmentation is not None else (),
        segmentation_reliable=segmentation.reliable if segmentation is not None else False,
        segmentation_reason=segmentation.reason if segmentation is not None else "not-run",
        recovery_reason=recovery_reason,
        assignments=assignments,
        relation_edges=relation_edges,
        node_order=(),
        fallback_reason=fallback_reason,
        used_panel_evidence=False,
        fallback_order=fallback_order,
        final_order=fallback_order,
    )
    return CalibrationResult(fallback_refs, diagnostic)


def _node_label(node: int, *, panel_count: int, region_index: int | None = None) -> str:
    if node < panel_count:
        return f"g{node:03d}"
    if region_index is None:
        raise ValueError("uncertain node label requires region index")
    return f"u{region_index:03d}"


def run_post_v2_calibration_candidate(
    pixels: Any,
    regions: Sequence[ExperimentRegion],
    *,
    page_height: int,
    config: CalibrationConfig,
) -> CalibrationResult:
    """Run the calibration candidate without consulting page IDs, text, or ground truth."""
    refs = tuple(regions)
    raw_regions = tuple(ref.region for ref in refs)
    refs_by_object = {id(ref.region): ref for ref in refs}
    if len(refs_by_object) != len(refs):
        raise ValueError("one runtime region object cannot back multiple experiment regions")

    fallback_raw = tuple(manga_tier_order(raw_regions, page_height=page_height))
    if len(raw_regions) < 2:
        return _fallback_result(
            refs_by_object,
            fallback_raw,
            segmentation=None,
            recovery_reason="not-attempted",
            fallback_reason="fewer-than-two-regions",
        )

    segmentation = segment_panel_groups(pixels)
    recovery_reason = "disabled"
    if config.c3_merged_frame_recovery:
        segmentation, recovery_reason = _recover_merged_frames(pixels, segmentation)
    if not segmentation.reliable:
        return _fallback_result(
            refs_by_object,
            fallback_raw,
            segmentation=segmentation,
            recovery_reason=recovery_reason,
            fallback_reason=segmentation.reason,
        )

    assignments = _assignment_observations(
        segmentation.boxes,
        refs,
        guarded=config.c1_boundary_guard,
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
        return _fallback_result(
            refs_by_object,
            fallback_raw,
            segmentation=segmentation,
            recovery_reason=recovery_reason,
            assignments=assignments,
            fallback_reason="insufficient-confident-panel-groups",
        )

    fallback_position = {id(region): index for index, region in enumerate(fallback_raw)}
    panel_ranks = tuple(
        min(
            (fallback_position[id(raw_regions[index])] for index in group),
            default=None,
        )
        for group in groups
    )
    hard_edges = _panel_precedence_edges(segmentation.boxes)
    panel_order = _hard_panel_order(segmentation.boxes, panel_ranks)
    if panel_order is None:
        return _fallback_result(
            refs_by_object,
            fallback_raw,
            segmentation=segmentation,
            recovery_reason=recovery_reason,
            assignments=assignments,
            fallback_reason="precedence-cycle",
        )

    panel_count = len(segmentation.boxes)
    uncertain_by_node = {
        panel_count + offset: region_index for offset, region_index in enumerate(uncertain)
    }
    graph_edges: list[tuple[int, int]] = [
        (edge.source_index, edge.target_index) for edge in hard_edges
    ]
    relation_diagnostics: list[CalibrationRelationEdge] = []
    active_panel_order = tuple(index for index in panel_order if groups[index])

    if config.c2_uncertain_relations:
        for offset, region_index in enumerate(uncertain):
            node_index = panel_count + offset
            proposed, status = _uncertain_relation_edges(
                region_index=region_index,
                assignment=assignments[region_index],
                raw_regions=raw_regions,
                boxes=segmentation.boxes,
                groups=groups,
                hard_edges=hard_edges,
                active_panel_order=active_panel_order,
                uncertain_node=node_index,
            )
            if status == "conflict":
                return _fallback_result(
                    refs_by_object,
                    fallback_raw,
                    segmentation=segmentation,
                    recovery_reason=recovery_reason,
                    assignments=assignments,
                    relation_edges=tuple(relation_diagnostics),
                    fallback_reason="uncertain-relation-conflict",
                )
            for source, target, rule in proposed:
                graph_edges.append((source, target))
                source_region = uncertain_by_node.get(source)
                target_region = uncertain_by_node.get(target)
                relation_diagnostics.append(
                    CalibrationRelationEdge(
                        _node_label(
                            source,
                            panel_count=panel_count,
                            region_index=source_region,
                        ),
                        _node_label(
                            target,
                            panel_count=panel_count,
                            region_index=target_region,
                        ),
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
        panel_count + len(uncertain),
        tuple(graph_edges),
        tie_key=tie_key,
    )
    if node_order is None:
        return _fallback_result(
            refs_by_object,
            fallback_raw,
            segmentation=segmentation,
            recovery_reason=recovery_reason,
            assignments=assignments,
            relation_edges=tuple(relation_diagnostics),
            fallback_reason="precedence-cycle",
        )

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

    if len(ordered_raw) != len(raw_regions):
        raise AssertionError("calibration candidate changed the OCR region count")
    if {id(region) for region in ordered_raw} != {id(region) for region in raw_regions}:
        raise AssertionError("calibration candidate changed the OCR region set")

    ordered_refs = tuple(refs_by_object[id(region)] for region in ordered_raw)
    fallback_order = tuple(refs_by_object[id(region)].region_id for region in fallback_raw)
    final_order = tuple(ref.region_id for ref in ordered_refs)
    diagnostic = CalibrationDiagnostic(
        segmentation_boxes=segmentation.boxes,
        segmentation_reliable=True,
        segmentation_reason=segmentation.reason,
        recovery_reason=recovery_reason,
        assignments=assignments,
        relation_edges=tuple(relation_diagnostics),
        node_order=tuple(node_labels),
        fallback_reason=None,
        used_panel_evidence=True,
        fallback_order=fallback_order,
        final_order=final_order,
    )
    return CalibrationResult(ordered_refs, diagnostic)
