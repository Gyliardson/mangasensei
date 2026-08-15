"""Deterministic local panel grouping and manga reading-order resolution."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import median
from typing import Any

_MIN_TIER_BAND_PAGE_FRACTION = 0.02
_MAX_TIER_BAND_PAGE_FRACTION = 0.12
_TIER_BAND_REGION_HEIGHT_FRACTION = 0.5
_MAX_PANEL_GROUPS = 16
_MIN_PANEL_AREA_FRACTION = 0.02
_MIN_CONTOUR_AREA_FRACTION = 0.025
_MIN_PANEL_WIDTH_FRACTION = 0.10
_MIN_PANEL_HEIGHT_FRACTION = 0.05
_MAX_AMBIGUOUS_OVERLAP = 0.20
_MIN_NESTED_OVERLAP = 0.85
_MAX_SPLIT_DEPTH = 6


@dataclass(frozen=True, slots=True)
class PanelBox:
    """Axis-aligned panel/group candidate used only during reading-order resolution."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


@dataclass(frozen=True, slots=True)
class PanelSegmentation:
    boxes: tuple[PanelBox, ...]
    reliable: bool
    reason: str


@dataclass(frozen=True, slots=True)
class PanelAssignment:
    groups: tuple[tuple[int, ...], ...]
    reliable: bool
    reason: str


@dataclass(frozen=True, slots=True)
class ReadingOrderResult:
    regions: tuple[Any, ...]
    used_panel_evidence: bool
    fallback_reason: str | None
    panel_count: int


@dataclass(frozen=True, slots=True)
class _ReadingOrderItem:
    source_index: int
    region: Any
    x_center: float
    y_top: float
    height: float


@dataclass(frozen=True, slots=True)
class _OverlapEvidence:
    numerator: int
    denominator: int

    @property
    def value(self) -> float:
        return self.numerator / self.denominator if self.denominator > 0 else 0.0


@dataclass(frozen=True, slots=True)
class _PanelPrecedenceEdge:
    source_index: int
    target_index: int
    rule: str
    x_overlap: _OverlapEvidence
    y_overlap: _OverlapEvidence


@dataclass(frozen=True, slots=True)
class _PanelFlowV1Resolution:
    fallback: tuple[Any, ...]
    segmentation: PanelSegmentation | None
    assignment: PanelAssignment | None
    fallback_ranks: tuple[int | None, ...]
    precedence_edges: tuple[_PanelPrecedenceEdge, ...]
    panel_order: tuple[int, ...] | None
    fallback_reason: str | None
    used_panel_evidence: bool


def _partition_manga_tiers(
    regions: Sequence[Any], *, page_height: int
) -> tuple[tuple[_ReadingOrderItem, ...], ...]:
    """Build the exact production manga-tier partition without local reordering."""
    if not regions:
        return ()

    items: list[_ReadingOrderItem] = []
    for source_index, region in enumerate(regions):
        x1, y1, x2, y2 = (float(value) for value in region.xyxy)
        items.append(
            _ReadingOrderItem(
                source_index=source_index,
                region=region,
                x_center=(x1 + x2) / 2,
                y_top=y1,
                height=max(1.0, y2 - y1),
            )
        )

    median_height = median(item.height for item in items)
    tier_band = max(
        page_height * _MIN_TIER_BAND_PAGE_FRACTION,
        min(
            page_height * _MAX_TIER_BAND_PAGE_FRACTION,
            median_height * _TIER_BAND_REGION_HEIGHT_FRACTION,
        ),
    )
    by_top = sorted(items, key=lambda item: (item.y_top, -item.x_center, item.source_index))

    tiers: list[tuple[float, list[_ReadingOrderItem]]] = []
    for item in by_top:
        if not tiers or item.y_top - tiers[-1][0] > tier_band:
            tiers.append((item.y_top, [item]))
        else:
            tiers[-1][1].append(item)
    return tuple(tuple(tier_items) for _, tier_items in tiers)


def manga_tier_order(regions: Sequence[Any], *, page_height: int) -> list[Any]:
    """Preserve the proven text-only manga-tier order used as the explicit fallback."""
    if len(regions) < 2:
        return list(regions)

    ordered: list[Any] = []
    for tier in _partition_manga_tiers(regions, page_height=page_height):
        tier_items = list(tier)
        tier_items.sort(key=lambda item: (-item.x_center, item.y_top, item.source_index))
        ordered.extend(item.region for item in tier_items)
    return ordered


def segment_panel_groups(pixels: Any) -> PanelSegmentation:
    """Recover conservative frame/group evidence using only local deterministic CV operations."""
    import cv2

    page_height, page_width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    lines = _line_segments(gray)
    boxes: list[PanelBox] = []
    for box in _sobel_external_boxes(pixels):
        boxes.extend(_split_box(box, lines, page_width, page_height))
    boxes.sort(key=lambda box: (box.y1, box.x1, box.y2, box.x2))
    return _validate_boxes(boxes, page_width, page_height)


def _candidate_group_indices(boxes: Sequence[PanelBox], region: Any) -> tuple[int, ...]:
    """Return the exact production strict-center containment memberships."""
    x1, y1, x2, y2 = (float(value) for value in region.xyxy)
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    return tuple(
        index
        for index, box in enumerate(boxes)
        if box.x1 <= center_x <= box.x2 and box.y1 <= center_y <= box.y2
    )


def assign_regions_to_groups(
    boxes: Sequence[PanelBox], regions: Sequence[Any]
) -> PanelAssignment:
    """Assign by strict center containment; ambiguity forces a page-level fallback."""
    groups: list[list[int]] = [[] for _ in boxes]
    for region_index, region in enumerate(regions):
        matches = _candidate_group_indices(boxes, region)
        if len(matches) != 1:
            return PanelAssignment(
                groups=tuple(tuple(group) for group in groups),
                reliable=False,
                reason="region-unassigned-or-ambiguous",
            )
        groups[matches[0]].append(region_index)

    if sum(bool(group) for group in groups) < 2:
        return PanelAssignment(
            groups=tuple(tuple(group) for group in groups),
            reliable=False,
            reason="fewer-than-two-nonempty-groups",
        )
    return PanelAssignment(
        groups=tuple(tuple(group) for group in groups),
        reliable=True,
        reason="reliable",
    )


def _overlap_evidence(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> _OverlapEvidence:
    numerator = max(0, min(first_end, second_end) - max(first_start, second_start))
    denominator = min(first_end - first_start, second_end - second_start)
    if denominator <= 0:
        return _OverlapEvidence(0, 1)
    return _OverlapEvidence(numerator, denominator)


def _panel_precedence_edges(boxes: Sequence[PanelBox]) -> tuple[_PanelPrecedenceEdge, ...]:
    """Build the exact edge set consumed by production panel precedence."""
    edges: list[_PanelPrecedenceEdge] = []
    seen: set[tuple[int, int]] = set()
    for first_index, first in enumerate(boxes):
        for second_index in range(first_index + 1, len(boxes)):
            second = boxes[second_index]
            x_evidence = _overlap_evidence(first.x1, first.x2, second.x1, second.x2)
            y_evidence = _overlap_evidence(first.y1, first.y2, second.y1, second.y2)
            x_overlap = x_evidence.value
            y_overlap = y_evidence.value
            edge: tuple[int, int] | None = None
            rule: str | None = None

            if y_overlap >= 0.25 and x_overlap <= 0.15:
                edge = (
                    (first_index, second_index)
                    if first.center[0] > second.center[0]
                    else (second_index, first_index)
                )
                rule = "same-level-right-before-left"
            elif x_overlap >= 0.25 and y_overlap <= 0.20:
                edge = (
                    (first_index, second_index)
                    if first.center[1] < second.center[1]
                    else (second_index, first_index)
                )
                rule = "aligned-top-before-bottom"
            elif first.y2 <= second.y1:
                edge = (first_index, second_index)
                rule = "nonoverlap-top-before-bottom"
            elif second.y2 <= first.y1:
                edge = (second_index, first_index)
                rule = "nonoverlap-top-before-bottom"

            if edge is not None and rule is not None and edge not in seen:
                seen.add(edge)
                edges.append(
                    _PanelPrecedenceEdge(
                        source_index=edge[0],
                        target_index=edge[1],
                        rule=rule,
                        x_overlap=x_evidence,
                        y_overlap=y_evidence,
                    )
                )
    return tuple(edges)


def _deterministic_topological_order(
    node_count: int,
    edges: Sequence[tuple[int, int]],
    *,
    tie_key: Callable[[int], tuple[Any, ...]],
) -> tuple[int, ...] | None:
    outgoing: list[set[int]] = [set() for _ in range(node_count)]
    indegree = [0 for _ in range(node_count)]
    for source, target in edges:
        if target not in outgoing[source]:
            outgoing[source].add(target)
            indegree[target] += 1

    ready = sorted((index for index, value in enumerate(indegree) if value == 0), key=tie_key)
    ordered: list[int] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=tie_key)
    if len(ordered) != node_count:
        return None
    return tuple(ordered)


def panel_precedence_order(
    boxes: Sequence[PanelBox], fallback_ranks: Sequence[int | None]
) -> tuple[int, ...] | None:
    """Topologically order groups using explicit vertical and right-to-left precedence."""
    if len(boxes) != len(fallback_ranks):
        raise ValueError("panel boxes and fallback ranks must have matching lengths")

    def tie_key(index: int) -> tuple[float, int, int, int]:
        fallback_rank = fallback_ranks[index]
        rank = float(fallback_rank) if fallback_rank is not None else float("inf")
        box = boxes[index]
        return (rank, box.y1, -box.x2, index)

    edges = _panel_precedence_edges(boxes)
    return _deterministic_topological_order(
        len(boxes),
        tuple((edge.source_index, edge.target_index) for edge in edges),
        tie_key=tie_key,
    )


def _resolve_panel_flow_v1(
    pixels: Any, regions: Sequence[Any], *, page_height: int
) -> _PanelFlowV1Resolution:
    fallback = tuple(manga_tier_order(regions, page_height=page_height))
    if len(regions) < 2:
        return _PanelFlowV1Resolution(
            fallback=fallback,
            segmentation=None,
            assignment=None,
            fallback_ranks=(),
            precedence_edges=(),
            panel_order=None,
            fallback_reason="fewer-than-two-regions",
            used_panel_evidence=False,
        )

    segmentation = segment_panel_groups(pixels)
    if not segmentation.reliable:
        return _PanelFlowV1Resolution(
            fallback=fallback,
            segmentation=segmentation,
            assignment=None,
            fallback_ranks=(),
            precedence_edges=(),
            panel_order=None,
            fallback_reason=segmentation.reason,
            used_panel_evidence=False,
        )

    assignment = assign_regions_to_groups(segmentation.boxes, regions)
    if not assignment.reliable:
        return _PanelFlowV1Resolution(
            fallback=fallback,
            segmentation=segmentation,
            assignment=assignment,
            fallback_ranks=(),
            precedence_edges=(),
            panel_order=None,
            fallback_reason=assignment.reason,
            used_panel_evidence=False,
        )

    fallback_position = {id(region): index for index, region in enumerate(fallback)}
    fallback_ranks = tuple(
        min((fallback_position[id(regions[index])] for index in group), default=None)
        for group in assignment.groups
    )
    precedence_edges = _panel_precedence_edges(segmentation.boxes)
    panel_order = panel_precedence_order(segmentation.boxes, fallback_ranks)
    if panel_order is None:
        return _PanelFlowV1Resolution(
            fallback=fallback,
            segmentation=segmentation,
            assignment=assignment,
            fallback_ranks=fallback_ranks,
            precedence_edges=precedence_edges,
            panel_order=None,
            fallback_reason="precedence-cycle",
            used_panel_evidence=False,
        )
    return _PanelFlowV1Resolution(
        fallback=fallback,
        segmentation=segmentation,
        assignment=assignment,
        fallback_ranks=fallback_ranks,
        precedence_edges=precedence_edges,
        panel_order=panel_order,
        fallback_reason=None,
        used_panel_evidence=True,
    )


def _materialize_panel_flow_v1(
    resolution: _PanelFlowV1Resolution,
    regions: Sequence[Any],
    *,
    page_height: int,
) -> tuple[Any, ...]:
    if not resolution.used_panel_evidence:
        return resolution.fallback
    assert resolution.assignment is not None
    assert resolution.panel_order is not None
    ordered: list[Any] = []
    for group_index in resolution.panel_order:
        group_regions = [regions[index] for index in resolution.assignment.groups[group_index]]
        ordered.extend(manga_tier_order(group_regions, page_height=page_height))
    if len(ordered) != len(regions) or {id(region) for region in ordered} != {
        id(region) for region in regions
    }:
        raise AssertionError("panel-aware reading order changed the OCR region set")
    return tuple(ordered)


def panel_aware_reading_order(
    pixels: Any, regions: Sequence[Any], *, page_height: int
) -> ReadingOrderResult:
    """Order by panel flow when evidence is reliable, otherwise use manga tiers verbatim."""
    resolution = _resolve_panel_flow_v1(pixels, regions, page_height=page_height)
    ordered = _materialize_panel_flow_v1(resolution, regions, page_height=page_height)
    panel_count = len(resolution.segmentation.boxes) if resolution.segmentation is not None else 0
    return ReadingOrderResult(
        regions=ordered,
        used_panel_evidence=resolution.used_panel_evidence,
        fallback_reason=resolution.fallback_reason,
        panel_count=panel_count,
    )


def _sobel_external_boxes(pixels: Any) -> tuple[PanelBox, ...]:
    import cv2

    page_height, page_width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    grad_x = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    sobel = cv2.addWeighted(
        cv2.convertScaleAbs(grad_x),
        0.5,
        cv2.convertScaleAbs(grad_y),
        0.5,
        0,
    )
    _, thresholded = cv2.threshold(sobel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresholded = cv2.morphologyEx(thresholded, cv2.MORPH_CLOSE, kernel, iterations=1)
    contours, _ = cv2.findContours(thresholded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    page_area = page_width * page_height
    boxes: list[PanelBox] = []
    for contour in contours:
        x, y, width, height = cv2.boundingRect(contour)
        area_fraction = width * height / page_area
        if area_fraction < _MIN_CONTOUR_AREA_FRACTION or area_fraction > 0.98:
            continue
        if width < page_width * _MIN_PANEL_WIDTH_FRACTION:
            continue
        if height < page_height * _MIN_PANEL_HEIGHT_FRACTION:
            continue
        boxes.append(PanelBox(x, y, x + width, y + height))
    return tuple(sorted(boxes, key=lambda box: (box.y1, box.x1, box.y2, box.x2)))


def _line_segments(gray: Any) -> tuple[tuple[float, float, float, float], ...]:
    import cv2
    import numpy as np

    detected = cv2.createLineSegmentDetector(0).detect(gray)
    if detected[0] is None:
        return ()
    lines = np.asarray(detected[0]).reshape(-1, 4)
    return tuple(
        (float(line[0]), float(line[1]), float(line[2]), float(line[3])) for line in lines
    )


def _cluster_separators(
    candidates: Sequence[tuple[float, float, float]], tolerance: float
) -> tuple[tuple[float, float, int], ...]:
    if not candidates:
        return ()

    groups: list[list[tuple[float, float, float]]] = []
    for candidate in sorted(candidates, key=lambda item: item[1]):
        if groups:
            mean_coordinate = sum(item[1] for item in groups[-1]) / len(groups[-1])
            if abs(candidate[1] - mean_coordinate) <= tolerance:
                groups[-1].append(candidate)
                continue
        groups.append([candidate])

    clustered: list[tuple[float, float, int]] = []
    for group in groups:
        total_weight = sum(item[2] for item in group)
        coordinate = sum(item[1] * item[2] for item in group) / total_weight
        clustered.append((max(item[0] for item in group), coordinate, len(group)))
    return tuple(clustered)


def _split_box(
    box: PanelBox,
    lines: Sequence[tuple[float, float, float, float]],
    page_width: int,
    page_height: int,
    *,
    depth: int = 0,
) -> tuple[PanelBox, ...]:
    if depth >= _MAX_SPLIT_DEPTH:
        return (box,)

    vertical: list[tuple[float, float, float]] = []
    horizontal: list[tuple[float, float, float]] = []
    for x1, y1, x2, y2 in lines:
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        if not (
            box.x1 - 3 <= center_x <= box.x2 + 3
            and box.y1 - 3 <= center_y <= box.y2 + 3
        ):
            continue

        delta_x = x2 - x1
        delta_y = y2 - y1
        length = math.hypot(delta_x, delta_y)

        if (
            abs(delta_y) > 1
            and abs(delta_x) <= 0.35 * abs(delta_y)
            and length >= 0.60 * box.height
        ):
            top = min(y1, y2)
            bottom = max(y1, y2)
            minimum_side = max(0.12 * box.width, 0.10 * page_width)
            if (
                top <= box.y1 + 0.08 * box.height
                and bottom >= box.y2 - 0.08 * box.height
                and box.x1 + minimum_side <= center_x <= box.x2 - minimum_side
            ):
                vertical.append((length / box.height, center_x, length))

        if (
            abs(delta_x) > 1
            and abs(delta_y) <= 0.35 * abs(delta_x)
            and length >= 0.60 * box.width
        ):
            left = min(x1, x2)
            right = max(x1, x2)
            minimum_side = max(0.12 * box.height, 0.06 * page_height)
            if (
                left <= box.x1 + 0.08 * box.width
                and right >= box.x2 - 0.08 * box.width
                and box.y1 + minimum_side <= center_y <= box.y2 - minimum_side
            ):
                horizontal.append((length / box.width, center_y, length))

    candidates: list[tuple[str, tuple[float, float, int]]] = []
    for orientation, clustered in (
        ("vertical", _cluster_separators(vertical, max(8, 0.02 * box.width))),
        ("horizontal", _cluster_separators(horizontal, max(8, 0.02 * box.height))),
    ):
        for candidate in clustered:
            span_score, _, support = candidate
            if support >= 2 or span_score >= 0.90:
                candidates.append((orientation, candidate))

    if not candidates:
        return (box,)

    orientation, (_, coordinate, _) = max(
        candidates,
        key=lambda item: (item[1][0], item[1][2], item[0]),
    )
    if orientation == "vertical":
        children = (
            PanelBox(box.x1, box.y1, round(coordinate), box.y2),
            PanelBox(round(coordinate), box.y1, box.x2, box.y2),
        )
    else:
        children = (
            PanelBox(box.x1, box.y1, box.x2, round(coordinate)),
            PanelBox(box.x1, round(coordinate), box.x2, box.y2),
        )

    result: list[PanelBox] = []
    for child in children:
        result.extend(
            _split_box(child, lines, page_width, page_height, depth=depth + 1)
        )
    return tuple(result)


def _validate_boxes(
    boxes: Sequence[PanelBox], page_width: int, page_height: int
) -> PanelSegmentation:
    if len(boxes) < 2:
        return PanelSegmentation(tuple(boxes), False, "fewer-than-two-groups")
    if len(boxes) > _MAX_PANEL_GROUPS:
        return PanelSegmentation(tuple(boxes), False, "too-many-groups")

    page_area = page_width * page_height
    for box in boxes:
        if box.area < page_area * _MIN_PANEL_AREA_FRACTION:
            return PanelSegmentation(tuple(boxes), False, "group-too-small")
        if box.width < page_width * _MIN_PANEL_WIDTH_FRACTION:
            return PanelSegmentation(tuple(boxes), False, "group-too-narrow")
        if box.height < page_height * _MIN_PANEL_HEIGHT_FRACTION:
            return PanelSegmentation(tuple(boxes), False, "group-too-short")

    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            intersection_width = max(0, min(first.x2, second.x2) - max(first.x1, second.x1))
            intersection_height = max(0, min(first.y2, second.y2) - max(first.y1, second.y1))
            intersection_area = intersection_width * intersection_height
            if intersection_area == 0:
                continue
            overlap = intersection_area / min(first.area, second.area)
            if overlap >= _MIN_NESTED_OVERLAP:
                return PanelSegmentation(tuple(boxes), False, "nested-or-inset-evidence")
            if overlap > _MAX_AMBIGUOUS_OVERLAP:
                return PanelSegmentation(tuple(boxes), False, "ambiguous-overlap")

    return PanelSegmentation(tuple(boxes), True, "reliable")


def _overlap_ratio(
    first_start: int,
    first_end: int,
    second_start: int,
    second_end: int,
) -> float:
    return _overlap_evidence(first_start, first_end, second_start, second_end).value
