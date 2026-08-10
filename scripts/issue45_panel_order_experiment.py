from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np
from PIL import Image

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    MangaImageTranslatorEngine,
    _decode_rgb,
    _manga_reading_order,
)
from mangasensei.ocr.contracts import OcrImage

FIXTURE_ROOT = Path("tests/fixtures/ocr/real_manga/black_jack")
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
PAGE21_PATH = "v01/black_jack_v01_pdf021.jpg"


@dataclass(frozen=True, slots=True)
class Box:
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


@dataclass(slots=True)
class RegionProxy:
    name: str
    xyxy: tuple[int, int, int, int]
    source_index: int
    orientation: str = "vertical"


@dataclass(frozen=True, slots=True)
class Segmentation:
    name: str
    boxes: tuple[Box, ...]
    reliable: bool
    reason: str


@dataclass(frozen=True, slots=True)
class Assignment:
    groups: tuple[tuple[int, ...], ...]
    reliable: bool
    reason: str


def _intersection(a: Box, b: Box) -> tuple[int, int]:
    return (
        max(0, min(a.x2, b.x2) - max(a.x1, b.x1)),
        max(0, min(a.y2, b.y2) - max(a.y1, b.y1)),
    )


def _validate_boxes(name: str, boxes: Sequence[Box], width: int, height: int) -> Segmentation:
    if len(boxes) < 2:
        return Segmentation(name, tuple(boxes), False, "fewer-than-two-groups")
    if len(boxes) > 16:
        return Segmentation(name, tuple(boxes), False, "too-many-groups")

    page_area = width * height
    for box in boxes:
        if box.area < page_area * 0.02:
            return Segmentation(name, tuple(boxes), False, "group-too-small")
        if box.width < width * 0.10 or box.height < height * 0.05:
            return Segmentation(name, tuple(boxes), False, "group-too-narrow")

    for index, first in enumerate(boxes):
        for second in boxes[index + 1 :]:
            iw, ih = _intersection(first, second)
            intersection_area = iw * ih
            if intersection_area == 0:
                continue
            overlap = intersection_area / min(first.area, second.area)
            if overlap >= 0.85:
                return Segmentation(name, tuple(boxes), False, "nested-or-inset-evidence")
            if overlap > 0.20:
                return Segmentation(name, tuple(boxes), False, "ambiguous-overlap")

    return Segmentation(name, tuple(boxes), True, "reliable")


def _sobel_external_boxes(pixels: np.ndarray) -> tuple[Box, ...]:
    height, width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    grad_x = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    sobel = cv2.addWeighted(
        cv2.convertScaleAbs(grad_x), 0.5, cv2.convertScaleAbs(grad_y), 0.5, 0
    )
    _, thresholded = cv2.threshold(
        sobel, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    thresholded = cv2.morphologyEx(
        thresholded, cv2.MORPH_CLOSE, kernel, iterations=1
    )
    contours, _ = cv2.findContours(
        thresholded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    boxes: list[Box] = []
    page_area = width * height
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        area_fraction = box_width * box_height / page_area
        if area_fraction < 0.025 or area_fraction > 0.98:
            continue
        if box_width < width * 0.10 or box_height < height * 0.05:
            continue
        boxes.append(Box(x, y, x + box_width, y + box_height))
    return tuple(
        sorted(boxes, key=lambda item: (item.y1, item.x1, item.y2, item.x2))
    )


def segment_contours(pixels: np.ndarray) -> Segmentation:
    height, width = pixels.shape[:2]
    return _validate_boxes(
        "sobel-contours", _sobel_external_boxes(pixels), width, height
    )


def _line_segments(gray: np.ndarray) -> tuple[tuple[float, float, float, float], ...]:
    detected = cv2.createLineSegmentDetector(0).detect(gray)
    if detected[0] is None:
        return ()
    return tuple(
        tuple(float(value) for value in line) for line in detected[0][:, 0, :]
    )


def _cluster_separators(
    candidates: Sequence[tuple[float, float, float]],
    tolerance: float,
) -> tuple[tuple[float, float, int], ...]:
    if not candidates:
        return ()
    sorted_candidates = sorted(candidates, key=lambda item: item[1])
    groups: list[list[tuple[float, float, float]]] = []
    for candidate in sorted_candidates:
        if groups:
            mean_coord = statistics.fmean(item[1] for item in groups[-1])
            if abs(candidate[1] - mean_coord) <= tolerance:
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
    box: Box,
    lines: Sequence[tuple[float, float, float, float]],
    page_width: int,
    page_height: int,
    *,
    depth: int = 0,
) -> tuple[Box, ...]:
    if depth >= 6:
        return (box,)

    width = box.width
    height = box.height
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
            and length >= 0.60 * height
        ):
            top = min(y1, y2)
            bottom = max(y1, y2)
            split_x = center_x
            min_side = max(0.12 * width, 0.10 * page_width)
            if (
                top <= box.y1 + 0.08 * height
                and bottom >= box.y2 - 0.08 * height
                and box.x1 + min_side <= split_x <= box.x2 - min_side
            ):
                vertical.append((length / height, split_x, length))

        if (
            abs(delta_x) > 1
            and abs(delta_y) <= 0.35 * abs(delta_x)
            and length >= 0.60 * width
        ):
            left = min(x1, x2)
            right = max(x1, x2)
            split_y = center_y
            min_side = max(0.12 * height, 0.06 * page_height)
            if (
                left <= box.x1 + 0.08 * width
                and right >= box.x2 - 0.08 * width
                and box.y1 + min_side <= split_y <= box.y2 - min_side
            ):
                horizontal.append((length / width, split_y, length))

    candidates: list[tuple[str, tuple[float, float, int]]] = []
    for orientation, clustered in (
        ("vertical", _cluster_separators(vertical, max(8, 0.02 * width))),
        ("horizontal", _cluster_separators(horizontal, max(8, 0.02 * height))),
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
            Box(box.x1, box.y1, round(coordinate), box.y2),
            Box(round(coordinate), box.y1, box.x2, box.y2),
        )
    else:
        children = (
            Box(box.x1, box.y1, box.x2, round(coordinate)),
            Box(box.x1, round(coordinate), box.x2, box.y2),
        )

    result: list[Box] = []
    for child in children:
        result.extend(
            _split_box(
                child,
                lines,
                page_width,
                page_height,
                depth=depth + 1,
            )
        )
    return tuple(result)


def segment_contours_with_lines(pixels: np.ndarray) -> Segmentation:
    height, width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    lines = _line_segments(gray)
    boxes: list[Box] = []
    for box in _sobel_external_boxes(pixels):
        boxes.extend(_split_box(box, lines, width, height))
    boxes.sort(key=lambda item: (item.y1, item.x1, item.y2, item.x2))
    return _validate_boxes(
        "sobel-contours+strict-lsd", boxes, width, height
    )


def _white_runs(
    values: np.ndarray, threshold: float, minimum: int
) -> tuple[tuple[int, int], ...]:
    mask = values >= threshold
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(mask):
        if value and start is None:
            start = index
        elif not value and start is not None:
            if index - start >= minimum:
                runs.append((start, index))
            start = None
    if start is not None and len(mask) - start >= minimum:
        runs.append((start, len(mask)))
    return tuple(runs)


def segment_white_gutters(pixels: np.ndarray) -> Segmentation:
    height, width = pixels.shape[:2]
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    white = gray >= 248
    column_runs = _white_runs(
        white.mean(axis=0), 0.965, max(5, round(width * 0.006))
    )
    row_runs = _white_runs(
        white.mean(axis=1), 0.965, max(5, round(height * 0.006))
    )

    split_x = [
        round((start + end) / 2)
        for start, end in column_runs
        if width * 0.10 < (start + end) / 2 < width * 0.90
    ]
    split_y = [
        round((start + end) / 2)
        for start, end in row_runs
        if height * 0.08 < (start + end) / 2 < height * 0.92
    ]
    if not split_x and not split_y:
        return Segmentation("white-gutters", (), False, "no-reliable-gutter")

    xs = [0, *split_x, width]
    ys = [0, *split_y, height]
    boxes: list[Box] = []
    for y1, y2 in zip(ys, ys[1:]):
        for x1, x2 in zip(xs, xs[1:]):
            if (x2 - x1) >= width * 0.10 and (y2 - y1) >= height * 0.05:
                boxes.append(Box(x1, y1, x2, y2))
    return _validate_boxes("white-gutters", boxes, width, height)


def _center_of_region(region: Any) -> tuple[float, float]:
    x1, y1, x2, y2 = (float(value) for value in region.xyxy)
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def assign_regions(boxes: Sequence[Box], regions: Sequence[Any]) -> Assignment:
    groups: list[list[int]] = [[] for _ in boxes]
    for region_index, region in enumerate(regions):
        center_x, center_y = _center_of_region(region)
        matches = [
            index
            for index, box in enumerate(boxes)
            if box.x1 <= center_x <= box.x2 and box.y1 <= center_y <= box.y2
        ]
        if len(matches) != 1:
            return Assignment(
                tuple(tuple(group) for group in groups),
                False,
                "region-unassigned-or-ambiguous",
            )
        groups[matches[0]].append(region_index)

    if sum(bool(group) for group in groups) < 2:
        return Assignment(
            tuple(tuple(group) for group in groups),
            False,
            "fewer-than-two-nonempty-groups",
        )
    return Assignment(tuple(tuple(group) for group in groups), True, "reliable")


def upstream_row_order(boxes: Sequence[Box]) -> tuple[int, ...]:
    if not boxes:
        return ()
    average_height = statistics.fmean(box.height for box in boxes)
    y_threshold = max(10.0, average_height * 0.30)
    remaining = sorted(range(len(boxes)), key=lambda index: (boxes[index].y1, index))
    ordered: list[int] = []
    while remaining:
        base_y = boxes[remaining[0]].y1
        row = [
            index
            for index in remaining
            if abs(boxes[index].y1 - base_y) <= y_threshold
        ]
        row_set = set(row)
        remaining = [index for index in remaining if index not in row_set]
        row.sort(key=lambda index: (-boxes[index].x1, boxes[index].y1, index))
        ordered.extend(row)
    return tuple(ordered)


def _overlap_ratio(
    first_start: int, first_end: int, second_start: int, second_end: int
) -> float:
    overlap = max(0, min(first_end, second_end) - max(first_start, second_start))
    denominator = min(first_end - first_start, second_end - second_start)
    return 0.0 if denominator <= 0 else overlap / denominator


def graph_panel_order(
    boxes: Sequence[Box],
    fallback_ranks: Sequence[int | None] | None = None,
) -> tuple[int, ...] | None:
    count = len(boxes)
    outgoing: list[set[int]] = [set() for _ in boxes]
    indegree = [0 for _ in boxes]

    for first_index in range(count):
        first = boxes[first_index]
        for second_index in range(first_index + 1, count):
            second = boxes[second_index]
            x_overlap = _overlap_ratio(
                first.x1, first.x2, second.x1, second.x2
            )
            y_overlap = _overlap_ratio(
                first.y1, first.y2, second.y1, second.y2
            )
            edge: tuple[int, int] | None = None

            if y_overlap >= 0.25 and x_overlap <= 0.15:
                first_center_x, _ = first.center
                second_center_x, _ = second.center
                edge = (
                    (first_index, second_index)
                    if first_center_x > second_center_x
                    else (second_index, first_index)
                )
            elif x_overlap >= 0.25 and y_overlap <= 0.20:
                _, first_center_y = first.center
                _, second_center_y = second.center
                edge = (
                    (first_index, second_index)
                    if first_center_y < second_center_y
                    else (second_index, first_index)
                )
            elif first.y2 <= second.y1:
                edge = (first_index, second_index)
            elif second.y2 <= first.y1:
                edge = (second_index, first_index)

            if edge is not None and edge[1] not in outgoing[edge[0]]:
                outgoing[edge[0]].add(edge[1])
                indegree[edge[1]] += 1

    ranks = fallback_ranks or [None] * count

    def tie_key(index: int) -> tuple[float, int, int, int]:
        rank = ranks[index]
        fallback = float(rank) if rank is not None else float("inf")
        box = boxes[index]
        return (fallback, box.y1, -box.x2, index)

    ready = sorted(
        (index for index, value in enumerate(indegree) if value == 0),
        key=tie_key,
    )
    ordered: list[int] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(outgoing[current]):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=tie_key)

    if len(ordered) != count:
        return None
    return tuple(ordered)


def panel_aware_order(
    pixels: np.ndarray,
    regions: Sequence[Any],
    *,
    page_height: int,
) -> tuple[list[Any], dict[str, Any]]:
    fallback = list(_manga_reading_order(regions, page_height=page_height))
    fallback_position = {id(region): index for index, region in enumerate(fallback)}
    segmentation = segment_contours_with_lines(pixels)
    if not segmentation.reliable:
        return fallback, {
            "used_panel_evidence": False,
            "fallback_reason": segmentation.reason,
            "group_count": len(segmentation.boxes),
        }

    assignment = assign_regions(segmentation.boxes, regions)
    if not assignment.reliable:
        return fallback, {
            "used_panel_evidence": False,
            "fallback_reason": assignment.reason,
            "group_count": len(segmentation.boxes),
        }

    group_ranks: list[int | None] = []
    for group in assignment.groups:
        if not group:
            group_ranks.append(None)
        else:
            group_ranks.append(
                min(fallback_position[id(regions[index])] for index in group)
            )

    group_order = graph_panel_order(segmentation.boxes, group_ranks)
    if group_order is None:
        return fallback, {
            "used_panel_evidence": False,
            "fallback_reason": "precedence-cycle",
            "group_count": len(segmentation.boxes),
        }

    ordered: list[Any] = []
    for group_index in group_order:
        members = [regions[index] for index in assignment.groups[group_index]]
        ordered.extend(_manga_reading_order(members, page_height=page_height))

    if len(ordered) != len(regions) or {id(region) for region in ordered} != {
        id(region) for region in regions
    }:
        raise AssertionError("panel-aware order changed the region set")

    return ordered, {
        "used_panel_evidence": True,
        "fallback_reason": None,
        "group_count": len(segmentation.boxes),
        "boxes": [
            [box.x1, box.y1, box.x2, box.y2] for box in segmentation.boxes
        ],
        "group_order": list(group_order),
    }


def _draw_frame(
    image: np.ndarray,
    box: Box,
    *,
    gap: tuple[str, int, int] | None = None,
) -> None:
    color = (0, 0, 0)
    thickness = 6
    if gap is None:
        cv2.rectangle(
            image, (box.x1, box.y1), (box.x2, box.y2), color, thickness
        )
        return
    side, start, end = gap
    cv2.line(image, (box.x1, box.y1), (box.x2, box.y1), color, thickness)
    cv2.line(image, (box.x1, box.y2), (box.x2, box.y2), color, thickness)
    cv2.line(image, (box.x1, box.y1), (box.x1, box.y2), color, thickness)
    cv2.line(image, (box.x2, box.y1), (box.x2, box.y2), color, thickness)
    if side == "right":
        cv2.line(
            image,
            (box.x2, max(box.y1, start)),
            (box.x2, min(box.y2, end)),
            (255, 255, 255),
            thickness + 2,
        )
    elif side == "top":
        cv2.line(
            image,
            (max(box.x1, start), box.y1),
            (min(box.x2, end), box.y1),
            (255, 255, 255),
            thickness + 2,
        )


def _synthetic_page() -> np.ndarray:
    return np.full((1000, 1000, 3), 255, dtype=np.uint8)


def _proxy(
    name: str,
    xyxy: tuple[int, int, int, int],
    index: int,
    orientation: str = "vertical",
) -> RegionProxy:
    return RegionProxy(
        name=name,
        xyxy=xyxy,
        source_index=index,
        orientation=orientation,
    )


def _names(items: Iterable[RegionProxy]) -> list[str]:
    return [item.name for item in items]


def run_synthetics() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    def evaluate(
        name: str,
        image: np.ndarray,
        regions: list[RegionProxy],
        expected: list[str] | None,
        *,
        expect_fallback: bool = False,
    ) -> None:
        baseline = list(
            _manga_reading_order(regions, page_height=image.shape[0])
        )
        candidate, metadata = panel_aware_order(
            image, regions, page_height=image.shape[0]
        )
        repeated, repeated_metadata = panel_aware_order(
            image.copy(), list(regions), page_height=image.shape[0]
        )
        passed = _names(candidate) == expected if expected is not None else True
        deterministic = (
            _names(candidate) == _names(repeated)
            and metadata == repeated_metadata
        )
        fallback_ok = (
            not metadata["used_panel_evidence"] if expect_fallback else True
        )
        cases.append(
            {
                "name": name,
                "baseline": _names(baseline),
                "candidate": _names(candidate),
                "expected": expected,
                "used_panel_evidence": metadata["used_panel_evidence"],
                "fallback_reason": metadata["fallback_reason"],
                "group_count": metadata["group_count"],
                "passed": bool(passed and deterministic and fallback_ok),
                "deterministic": deterministic,
            }
        )

    image = _synthetic_page()
    _draw_frame(image, Box(520, 50, 950, 480))
    _draw_frame(image, Box(50, 50, 480, 480))
    evaluate(
        "same-row-offset-text-starts",
        image,
        [
            _proxy("left", (130, 100, 230, 260), 0),
            _proxy("right", (740, 280, 840, 440), 1),
        ],
        ["right", "left"],
    )

    image = _synthetic_page()
    _draw_frame(image, Box(520, 50, 950, 500))
    _draw_frame(image, Box(50, 50, 480, 500))
    evaluate(
        "neighbor-first-text-substantially-lower",
        image,
        [
            _proxy("left", (120, 80, 220, 220), 0),
            _proxy("right", (760, 350, 850, 480), 1),
        ],
        ["right", "left"],
    )

    image = _synthetic_page()
    _draw_frame(image, Box(520, 50, 950, 700))
    _draw_frame(image, Box(50, 50, 480, 430))
    evaluate(
        "unequal-height-neighbors",
        image,
        [
            _proxy("left", (140, 100, 240, 260), 0),
            _proxy("right", (760, 330, 850, 540), 1),
        ],
        ["right", "left"],
    )

    image = _synthetic_page()
    _draw_frame(image, Box(520, 50, 950, 420))
    _draw_frame(image, Box(50, 50, 480, 420))
    _draw_frame(image, Box(50, 460, 950, 920))
    evaluate(
        "panel-with-no-ocr-text",
        image,
        [
            _proxy("upper-left", (130, 100, 230, 260), 0),
            _proxy("lower", (760, 550, 850, 760), 1),
        ],
        ["upper-left", "lower"],
    )

    image = _synthetic_page()
    _draw_frame(image, Box(520, 50, 950, 480))
    _draw_frame(image, Box(50, 50, 480, 480))
    evaluate(
        "mixed-text-orientation",
        image,
        [
            _proxy(
                "left-horizontal", (100, 100, 320, 180), 0, "horizontal"
            ),
            _proxy(
                "right-vertical", (760, 300, 850, 450), 1, "vertical"
            ),
        ],
        ["right-vertical", "left-horizontal"],
    )

    image = _synthetic_page()
    _draw_frame(image, Box(70, 60, 930, 920))
    _draw_frame(image, Box(610, 120, 880, 390))
    regions = [
        _proxy("outer", (150, 180, 260, 360), 0),
        _proxy("inset", (690, 180, 800, 330), 1),
    ]
    evaluate(
        "inset-panel",
        image,
        regions,
        _names(_manga_reading_order(regions, page_height=1000)),
        expect_fallback=True,
    )

    image = _synthetic_page()
    _draw_frame(image, Box(100, 100, 650, 650))
    _draw_frame(image, Box(450, 250, 900, 800))
    regions = [
        _proxy("first", (200, 180, 300, 340), 0),
        _proxy("second", (650, 400, 760, 560), 1),
    ]
    evaluate(
        "overlapping-ambiguous-panels",
        image,
        regions,
        _names(_manga_reading_order(regions, page_height=1000)),
        expect_fallback=True,
    )

    image = _synthetic_page()
    _draw_frame(
        image, Box(520, 50, 950, 480), gap=("right", 180, 280)
    )
    _draw_frame(
        image, Box(50, 50, 480, 480), gap=("top", 170, 300)
    )
    regions = [
        _proxy("left", (120, 100, 220, 260), 0),
        _proxy("right", (760, 300, 850, 450), 1),
    ]
    evaluate("missing-open-borders", image, regions, None)

    image = _synthetic_page()
    regions = [
        _proxy("upper-left", (120, 100, 220, 260), 0),
        _proxy("lower-right", (760, 600, 850, 800), 1),
    ]
    evaluate(
        "no-reliable-frame-evidence",
        image,
        regions,
        _names(_manga_reading_order(regions, page_height=1000)),
        expect_fallback=True,
    )

    simple_upper = [
        _proxy("lower-right", (760, 620, 850, 800), 0),
        _proxy("upper-left", (120, 130, 220, 300), 1),
        _proxy("upper-right", (760, 100, 850, 280), 2),
    ]
    simple_names = _names(
        _manga_reading_order(simple_upper, page_height=1000)
    )
    cases.append(
        {
            "name": "existing-top-bottom-tier-contract",
            "baseline": simple_names,
            "candidate": simple_names,
            "expected": ["upper-right", "upper-left", "lower-right"],
            "used_panel_evidence": False,
            "fallback_reason": "direct-fallback-contract",
            "group_count": 0,
            "passed": simple_names
            == ["upper-right", "upper-left", "lower-right"],
            "deterministic": True,
        }
    )

    same_tier = [
        _proxy("left", (120, 100, 220, 260), 0),
        _proxy("right", (760, 110, 850, 270), 1),
    ]
    same_names = _names(_manga_reading_order(same_tier, page_height=1000))
    cases.append(
        {
            "name": "existing-same-row-rtl-contract",
            "baseline": same_names,
            "candidate": same_names,
            "expected": ["right", "left"],
            "used_panel_evidence": False,
            "fallback_reason": "direct-fallback-contract",
            "group_count": 0,
            "passed": same_names == ["right", "left"],
            "deterministic": True,
        }
    )

    staggered = (
        Box(50, 50, 450, 550),
        Box(500, 250, 950, 700),
    )
    row_order = upstream_row_order(staggered)
    graph_order = graph_panel_order(staggered)
    solver = {
        "boxes": [
            [box.x1, box.y1, box.x2, box.y2] for box in staggered
        ],
        "row_heuristic": list(row_order),
        "precedence_graph": None if graph_order is None else list(graph_order),
        "expected": [1, 0],
        "row_passed": list(row_order) == [1, 0],
        "graph_passed": graph_order is not None
        and list(graph_order) == [1, 0],
    }

    return {
        "cases": cases,
        "all_passed": all(case["passed"] for case in cases),
        "solver": solver,
    }


def _region_proxies(result: Any) -> list[RegionProxy]:
    proxies: list[RegionProxy] = []
    for index, region in enumerate(result.regions):
        proxies.append(
            RegionProxy(
                name=str(index),
                xyxy=(
                    region.bbox.x,
                    region.bbox.y,
                    region.bbox.x + region.bbox.width,
                    region.bbox.y + region.bbox.height,
                ),
                source_index=index,
            )
        )
    return proxies


def _safe_region_fingerprint(region: Any) -> str:
    payload = {
        "text_sha256": hashlib.sha256(
            region.japanese_text.encode("utf-8")
        ).hexdigest(),
        "confidence": region.confidence,
        "bbox": region.bbox.model_dump(),
        "polygon": region.polygon,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _panel_label_page21(region: RegionProxy) -> str:
    center_x, center_y = _center_of_region(region)
    if center_y < 820 and center_x >= 700:
        return "upper-right"
    if center_y < 820:
        return "upper-left"
    if center_y < 1330:
        return "middle"
    if center_x >= 844:
        return "lower-right"
    if center_x >= 535:
        return "lower-middle"
    return "lower-left"


def _benchmark_order(
    pixels: np.ndarray,
    proxies: list[RegionProxy],
    page_height: int,
) -> dict[str, Any]:
    panel_aware_order(pixels, proxies, page_height=page_height)
    durations: list[float] = []
    tracemalloc.start()
    for _ in range(20):
        started = time.perf_counter_ns()
        panel_aware_order(pixels, proxies, page_height=page_height)
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    durations.sort()
    return {
        "median_ms": statistics.median(durations),
        "p95_ms": durations[
            max(0, math.ceil(0.95 * len(durations)) - 1)
        ],
        "python_peak_kib": peak / 1024,
        "iterations": len(durations),
    }


async def run_real_corpus() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    engine = MangaImageTranslatorEngine(
        model_cache=Path("var/models"),
        device="cpu",
    )
    fixtures: list[dict[str, Any]] = []
    fallback_count = 0
    page21_review: dict[str, Any] | None = None

    for fixture in manifest["fixtures"]:
        relative_path = str(fixture["file"])
        image_path = FIXTURE_ROOT / relative_path
        content = image_path.read_bytes()
        with Image.open(image_path) as source:
            dimensions = PageDimensions(
                width=source.width,
                height=source.height,
            )
        result = await engine.analyze(
            OcrImage(
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                media_type="image/jpeg",
                dimensions=dimensions,
            )
        )
        pixels = _decode_rgb(content)
        proxies = _region_proxies(result)

        contour_only = segment_contours(pixels)
        contour_lines = segment_contours_with_lines(pixels)
        gutters = segment_white_gutters(pixels)
        candidate, metadata = panel_aware_order(
            pixels,
            proxies,
            page_height=dimensions.height,
        )
        if not metadata["used_panel_evidence"]:
            fallback_count += 1

        fingerprint_by_index = [
            _safe_region_fingerprint(region) for region in result.regions
        ]
        before_fingerprints = sorted(fingerprint_by_index)
        after_fingerprints = sorted(
            fingerprint_by_index[proxy.source_index] for proxy in candidate
        )
        invariants = {
            "region_count_unchanged": len(candidate) == len(result.regions),
            "text_confidence_geometry_multiset_unchanged": (
                before_fingerprints == after_fingerprints
            ),
            "permutation_complete": sorted(
                proxy.source_index for proxy in candidate
            )
            == list(range(len(result.regions))),
        }

        fixtures.append(
            {
                "file": relative_path,
                "region_count": len(result.regions),
                "segmentation": {
                    "sobel_contours": {
                        "groups": len(contour_only.boxes),
                        "reliable": contour_only.reliable,
                        "reason": contour_only.reason,
                    },
                    "sobel_contours_strict_lsd": {
                        "groups": len(contour_lines.boxes),
                        "reliable": contour_lines.reliable,
                        "reason": contour_lines.reason,
                    },
                    "white_gutters": {
                        "groups": len(gutters.boxes),
                        "reliable": gutters.reliable,
                        "reason": gutters.reason,
                    },
                },
                "panel_order": {
                    "used_panel_evidence": metadata["used_panel_evidence"],
                    "fallback_reason": metadata["fallback_reason"],
                    "group_count": metadata["group_count"],
                    "before_indices": list(range(len(proxies))),
                    "after_indices": [
                        proxy.source_index for proxy in candidate
                    ],
                },
                "invariants": invariants,
                "performance": _benchmark_order(
                    pixels,
                    proxies,
                    dimensions.height,
                ),
            }
        )

        if relative_path == PAGE21_PATH:
            before_labels = [_panel_label_page21(proxy) for proxy in proxies]
            after_labels = [
                _panel_label_page21(proxy) for proxy in candidate
            ]
            upper_right_positions = [
                index
                for index, label in enumerate(after_labels)
                if label == "upper-right"
            ]
            upper_left_positions = [
                index
                for index, label in enumerate(after_labels)
                if label == "upper-left"
            ]
            lower_right_positions = [
                index
                for index, label in enumerate(after_labels)
                if label == "lower-right"
            ]
            lower_left_positions = [
                index
                for index, label in enumerate(after_labels)
                if label == "lower-left"
            ]
            before_upper_right = [
                index
                for index, label in enumerate(before_labels)
                if label == "upper-right"
            ]
            before_upper_left = [
                index
                for index, label in enumerate(before_labels)
                if label == "upper-left"
            ]
            page21_review = {
                "before_panel_labels": before_labels,
                "after_panel_labels": after_labels,
                "baseline_violates_upper_rtl": bool(
                    before_upper_right
                    and before_upper_left
                    and min(before_upper_left) < min(before_upper_right)
                ),
                "candidate_upper_rtl": bool(
                    upper_right_positions
                    and upper_left_positions
                    and max(upper_right_positions) < min(upper_left_positions)
                ),
                "candidate_lower_rtl": bool(
                    lower_right_positions
                    and lower_left_positions
                    and max(lower_right_positions) < min(lower_left_positions)
                ),
                "used_panel_evidence": metadata["used_panel_evidence"],
                "group_count": metadata["group_count"],
            }

    return {
        "opencv_version": cv2.__version__,
        "fixtures": fixtures,
        "fallback_count": fallback_count,
        "fallback_rate": fallback_count / len(fixtures),
        "page21_human_review_contract": page21_review,
        "all_invariants_passed": all(
            all(item["invariants"].values()) for item in fixtures
        ),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payload = {
        "experiment": "issue-45-panel-aware-reading-order-phase-1",
        "synthetic": run_synthetics(),
        "real_corpus": await run_real_corpus(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
