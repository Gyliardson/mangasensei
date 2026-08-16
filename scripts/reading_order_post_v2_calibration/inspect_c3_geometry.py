from __future__ import annotations

import json
import math

import cv2
import numpy as np
from PIL import Image
from scripts.reading_order_v2.contracts import arm_asset_paths
from scripts.reading_order_v2.validate_corpus import CORPUS_ROOT

from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    _FRAME_CLUSTER_FRACTION,
    _FRAME_MIN_SEGMENT_FRACTION,
    _cluster_axis_lines,
    _interval_coverage,
)
from mangasensei.ocr.reading_order import _line_segments, segment_panel_groups


def _inspect(page_id: str) -> dict[str, object]:
    image_path, _ = arm_asset_paths(CORPUS_ROOT, page_id)
    with Image.open(image_path) as opened:
        pixels = np.asarray(opened.convert("RGB"))
    segmentation = segment_panel_groups(pixels)
    if len(segmentation.boxes) != 1:
        return {
            "pageId": page_id,
            "segmentationReason": segmentation.reason,
            "boxes": [box.__dict__ for box in segmentation.boxes],
        }

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
    candidates: list[dict[str, object]] = []
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
                    candidates.append(
                        {
                            "box": [round(x1), round(y1), round(x2), round(y2)],
                            "coverage": [round(value, 4) for value in coverages],
                            "minCoverage": round(min(coverages), 4),
                        }
                    )

    candidates.sort(
        key=lambda item: (
            -float(item["minCoverage"]),
            tuple(item["box"]),
        )
    )
    return {
        "pageId": page_id,
        "segmentationReason": segmentation.reason,
        "mergedBox": [merged.x1, merged.y1, merged.x2, merged.y2],
        "horizontalLineCount": len(horizontal),
        "verticalLineCount": len(vertical),
        "horizontalClusters": [
            {
                "coordinate": round(cluster.coordinate, 2),
                "intervals": [
                    [round(start, 2), round(end, 2)]
                    for start, end in cluster.intervals
                ],
            }
            for cluster in horizontal_clusters
        ],
        "verticalClusters": [
            {
                "coordinate": round(cluster.coordinate, 2),
                "intervals": [
                    [round(start, 2), round(end, 2)]
                    for start, end in cluster.intervals
                ],
            }
            for cluster in vertical_clusters
        ],
        "topCandidates": candidates[:12],
    }


def main() -> None:
    print("C3 GEOMETRY PROBE — CALIBRATION ONLY; NO GT USED")
    for page_id in ("H10", "H16"):
        print(json.dumps(_inspect(page_id), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
