from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
    _OcrConfig,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.detection.default import (
    det_batch_forward_default,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.detection.default_utils import (
    dbnet_utils,
    imgproc,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    det_rearrange_forward,
)

DETECTION_SIZE = 2048
TEXT_THRESHOLD = 0.5
BOX_THRESHOLD = 0.7
UNCLIP_RATIO = 2.3
MINIMUM_CONFIDENCE = 0.2
EXPECTED_PAGE9_SHA256 = "072e3d9c2b54628a6de0c18a0ebe078817f30de545259ab3bcd0eb16974210dd"
TARGET_ROI = (160, 930, 290, 1220)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _xyxy(points: Any) -> list[int]:
    array = np.asarray(points)
    return [
        int(array[:, 0].min()),
        int(array[:, 1].min()),
        int(array[:, 0].max()),
        int(array[:, 1].max()),
    ]


def _intersects(box: list[int], roi: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = box
    rx0, ry0, rx1, ry1 = roi
    return x1 >= rx0 and x0 <= rx1 and y1 >= ry0 and y0 <= ry1


def _source_small_components(pixels: np.ndarray) -> list[dict[str, Any]]:
    x0, y0, x1, y1 = TARGET_ROI
    gray = cv2.cvtColor(pixels[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    binary = (gray < 100).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    components: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if not (3 <= width <= 8 and 3 <= height <= 8 and 8 <= area <= 40):
            continue
        components.append(
            {
                "source_bbox": [x + x0, y + y0, x + x0 + width, y + y0 + height],
                "area": area,
                "center": [
                    float(centroids[index][0] + x0),
                    float(centroids[index][1] + y0),
                ],
            }
        )
    return components


def _pred_numpy(db: Any) -> np.ndarray:
    pred = db[0, 0]
    if hasattr(pred, "detach"):
        pred = pred.detach().cpu().numpy()
    return np.asarray(pred, dtype=np.float32)


def _raw_detector_forward(pixels: np.ndarray) -> tuple[Any, float, float, bool]:
    db, _ = det_rearrange_forward(
        pixels,
        det_batch_forward_default,
        DETECTION_SIZE,
        4,
        device="cpu",
        verbose=False,
    )
    if db is not None:
        return db, 1.0, 1.0, True

    filtered = cv2.bilateralFilter(pixels, 17, 80, 80)
    resized, target_ratio, _, _, _ = imgproc.resize_aspect_ratio(
        filtered,
        DETECTION_SIZE,
        cv2.INTER_LINEAR,
        mag_ratio=1,
    )
    db, _ = det_batch_forward_default([resized], "cpu")
    coordinate_ratio = 1 / target_ratio
    return db, coordinate_ratio, coordinate_ratio, False


def _trace_segmentation(
    pixels: np.ndarray,
    db: Any,
    ratio_w: float,
    ratio_h: float,
) -> dict[str, Any]:
    pred = _pred_numpy(db)
    representer = dbnet_utils.SegDetectorRepresenter(
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        unclip_ratio=UNCLIP_RATIO,
    )
    bitmap = pred > TEXT_THRESHOLD
    contours, _ = cv2.findContours(
        (bitmap * 255).astype(np.uint8),
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    contour_records: list[dict[str, Any]] = []
    for contour_index, contour_raw in enumerate(contours[: representer.max_candidates]):
        contour = contour_raw.squeeze(1)
        points, short_side = representer.get_mini_boxes(contour)
        mapped = np.asarray(points, dtype=np.float64)
        mapped[:, 0] *= ratio_w
        mapped[:, 1] *= ratio_h
        source_box = _xyxy(mapped)
        if not _intersects(source_box, TARGET_ROI):
            continue

        passes_min_size = bool(short_side >= representer.min_size)
        score = (
            float(representer.box_score_fast(pred, contour))
            if passes_min_size
            else None
        )
        passes_box_threshold = bool(score is not None and score >= BOX_THRESHOLD)
        record: dict[str, Any] = {
            "contour_index": contour_index,
            "source_xyxy_pre_unclip": source_box,
            "short_side_pre_unclip": float(short_side),
            "score": score,
            "passes_min_size": passes_min_size,
            "passes_box_threshold": passes_box_threshold,
        }
        if passes_min_size and passes_box_threshold:
            expanded = representer.unclip(
                np.asarray(points),
                unclip_ratio=UNCLIP_RATIO,
            ).reshape(-1, 1, 2)
            expanded_points, expanded_short_side = representer.get_mini_boxes(expanded)
            mapped_expanded = np.asarray(expanded_points, dtype=np.float64)
            mapped_expanded[:, 0] *= ratio_w
            mapped_expanded[:, 1] *= ratio_h
            record["source_xyxy_post_unclip"] = _xyxy(mapped_expanded)
            record["short_side_post_unclip"] = float(expanded_short_side)
            record["passes_post_unclip_min_size"] = bool(
                expanded_short_side >= representer.min_size + 2
            )
        contour_records.append(record)

    pred_height, pred_width = pred.shape
    components: list[dict[str, Any]] = []
    for component in _source_small_components(pixels):
        source_box = component["source_bbox"]
        x0 = max(0, int(np.floor(source_box[0] / ratio_w)))
        y0 = max(0, int(np.floor(source_box[1] / ratio_h)))
        x1 = min(pred_width, int(np.ceil(source_box[2] / ratio_w)))
        y1 = min(pred_height, int(np.ceil(source_box[3] / ratio_h)))
        patch = pred[y0:y1, x0:x1]
        record = dict(component)
        record["detector_probability_max"] = float(patch.max()) if patch.size else 0.0
        record["detector_probability_mean"] = (
            float(patch.mean()) if patch.size else 0.0
        )
        record["crosses_text_threshold"] = bool(
            patch.size and float(patch.max()) > TEXT_THRESHOLD
        )
        components.append(record)

    return {
        "source_roi": list(TARGET_ROI),
        "text_threshold": TEXT_THRESHOLD,
        "box_threshold": BOX_THRESHOLD,
        "contours_intersecting_roi": contour_records,
        "source_small_components": components,
    }


def _geometry_key(points: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(points))


async def _trace_recognizer(
    detector: Any,
    recognizer: Any,
    pixels: np.ndarray,
) -> list[dict[str, Any]]:
    textlines, _, _ = await detector.detect(
        pixels,
        DETECTION_SIZE,
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        UNCLIP_RATIO,
        *_DETECTOR_FLAGS,
    )
    target_lines = [line for line in textlines if _intersects(list(line.xyxy), TARGET_ROI)]
    source_by_geometry = {
        _geometry_key(line.pts): index for index, line in enumerate(target_lines)
    }

    crop_records: dict[int, dict[str, Any]] = {}
    crop_input = copy.deepcopy(target_lines)
    for line, direction in recognizer._generate_text_direction(crop_input):
        crop = line.get_transformed_region(pixels, direction, 48)
        source_index = source_by_geometry.get(_geometry_key(line.pts))
        if source_index is None:
            continue
        crop_records[source_index] = {
            "direction": str(direction),
            "crop_width": int(crop.shape[1]),
            "crop_height": int(crop.shape[0]),
        }

    recognized_input = copy.deepcopy(target_lines)
    recognized = await recognizer.recognize(
        pixels,
        recognized_input,
        _OcrConfig(prob=MINIMUM_CONFIDENCE),
        _RECOGNIZER_FLAG,
    )
    recognized_by_geometry = {
        _geometry_key(line.pts): line for line in recognized if str(line.text).strip()
    }

    records: list[dict[str, Any]] = []
    for index, line in enumerate(target_lines):
        recognized_line = recognized_by_geometry.get(_geometry_key(line.pts))
        records.append(
            {
                "target_line_index": index,
                "xyxy": [int(value) for value in line.xyxy],
                "detector_score": float(line.prob),
                "crop": crop_records.get(index),
                "recognized": recognized_line is not None,
                "recognized_probability": (
                    float(recognized_line.prob) if recognized_line is not None else None
                ),
                "recognized_codepoints": (
                    len(str(recognized_line.text)) if recognized_line is not None else 0
                ),
            }
        )
    return records


async def _main(args: argparse.Namespace) -> None:
    content = args.page9.read_bytes()
    digest = _sha256(content)
    if digest != EXPECTED_PAGE9_SHA256:
        raise ValueError("page 9 bytes do not match the reviewed embedded JPEG SHA-256")

    pixels = _decode_rgb(content)
    if pixels.shape[:2] != (2000, 1414):
        raise ValueError(f"unexpected page 9 dimensions: {pixels.shape[:2]}")

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, _, manifest = await engine._ensure_loaded()
    db, ratio_w, ratio_h, rearranged = _raw_detector_forward(pixels)

    report = {
        "schema_version": 1,
        "privacy": "No recognized manga text or source image bytes are serialized.",
        "page9": {"sha256": digest, "width": 1414, "height": 2000},
        "production_config": {
            "detection_size": DETECTION_SIZE,
            "text_threshold": TEXT_THRESHOLD,
            "box_threshold": BOX_THRESHOLD,
            "unclip_ratio": UNCLIP_RATIO,
            "minimum_confidence": MINIMUM_CONFIDENCE,
            "device": "cpu",
        },
        "model_manifest_version": manifest.version,
        "upstream_commit": manifest.upstream_commit,
        "rearranged": rearranged,
        "coordinate_scale": {"x": ratio_w, "y": ratio_h},
        "segmentation": _trace_segmentation(pixels, db, ratio_w, ratio_h),
        "recognizer": await _trace_recognizer(detector, recognizer, pixels),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "target_contours": len(report["segmentation"]["contours_intersecting_roi"]),
                "target_source_small_components": len(
                    report["segmentation"]["source_small_components"]
                ),
                "target_textlines": len(report["recognizer"]),
            },
            sort_keys=True,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page9", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
