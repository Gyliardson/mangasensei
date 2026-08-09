from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import time
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

TEXT_THRESHOLD = 0.5
BOX_THRESHOLD = 0.7
UNCLIP_RATIO = 2.3
MINIMUM_CONFIDENCE = 0.2
EXPECTED_PAGE9_SHA256 = "072e3d9c2b54628a6de0c18a0ebe078817f30de545259ab3bcd0eb16974210dd"
TARGET_ROI = (160, 930, 290, 1220)
DETECTION_SIZES = (2048, 2560, 3072, 3584, 4096)


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


def _intersects(box: list[int]) -> bool:
    x0, y0, x1, y1 = box
    rx0, ry0, rx1, ry1 = TARGET_ROI
    return x1 >= rx0 and x0 <= rx1 and y1 >= ry0 and y0 <= ry1


def _source_interpuncts(pixels: np.ndarray) -> list[dict[str, Any]]:
    x0, y0, x1, y1 = TARGET_ROI
    gray = cv2.cvtColor(pixels[y0:y1, x0:x1], cv2.COLOR_RGB2GRAY)
    binary = (gray < 100).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(binary, 8)
    components: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        source_x0 = x + x0
        source_x1 = source_x0 + width
        source_y0 = y + y0
        source_y1 = source_y0 + height
        center_x = float(centroids[index][0] + x0)
        center_y = float(centroids[index][1] + y0)
        is_dot_column = (
            260 <= center_x <= 266
            and 3 <= width <= 6
            and 3 <= height <= 6
            and 12 <= area <= 24
        )
        if not is_dot_column:
            continue
        components.append(
            {
                "source_bbox": [source_x0, source_y0, source_x1, source_y1],
                "area": area,
                "center": [center_x, center_y],
            }
        )
    return components


def _pred_numpy(db: Any) -> np.ndarray:
    pred = db[0, 0]
    if hasattr(pred, "detach"):
        pred = pred.detach().cpu().numpy()
    return np.asarray(pred, dtype=np.float32)


def _forward(
    pixels: np.ndarray,
    detection_size: int,
) -> tuple[np.ndarray, float, float, dict[str, int]]:
    filtered = cv2.bilateralFilter(pixels, 17, 80, 80)
    resized, target_ratio, _, pad_w, pad_h = imgproc.resize_aspect_ratio(
        filtered,
        detection_size,
        cv2.INTER_LINEAR,
        mag_ratio=1,
    )
    started = time.perf_counter()
    db, _ = det_batch_forward_default([resized], "cpu")
    elapsed = time.perf_counter() - started
    pred = _pred_numpy(db)
    coordinate_ratio = 1 / target_ratio
    return (
        pred,
        coordinate_ratio,
        elapsed,
        {
            "width": int(resized.shape[1]),
            "height": int(resized.shape[0]),
            "pad_width": int(pad_w),
            "pad_height": int(pad_h),
        },
    )


def _sample_components(
    pred: np.ndarray,
    coordinate_ratio: float,
    components: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pred_height, pred_width = pred.shape
    output: list[dict[str, Any]] = []
    for component in components:
        source_box = component["source_bbox"]
        x0 = max(0, int(np.floor(source_box[0] / coordinate_ratio)))
        y0 = max(0, int(np.floor(source_box[1] / coordinate_ratio)))
        x1 = min(pred_width, int(np.ceil(source_box[2] / coordinate_ratio)))
        y1 = min(pred_height, int(np.ceil(source_box[3] / coordinate_ratio)))
        patch = pred[y0:y1, x0:x1]
        record = dict(component)
        record["probability_max"] = float(patch.max()) if patch.size else 0.0
        record["probability_mean"] = float(patch.mean()) if patch.size else 0.0
        record["crosses_text_threshold"] = bool(
            patch.size and float(patch.max()) > TEXT_THRESHOLD
        )
        output.append(record)
    return output


def _representer_target_boxes(
    pred: np.ndarray,
    coordinate_ratio: float,
) -> list[dict[str, Any]]:
    representer = dbnet_utils.SegDetectorRepresenter(
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        unclip_ratio=UNCLIP_RATIO,
    )
    db = pred[None, None, ...]
    height, width = pred.shape
    boxes, scores = representer({"shape": [(height, width)]}, db)
    target: list[dict[str, Any]] = []
    for box, score in zip(boxes[0], scores[0], strict=True):
        if not np.asarray(box).reshape(-1).sum():
            continue
        mapped = np.asarray(box, dtype=np.float64) * coordinate_ratio
        source_box = _xyxy(mapped)
        if not _intersects(source_box):
            continue
        target.append(
            {
                "xyxy": source_box,
                "score": float(score),
            }
        )
    return target


async def _production_target(
    detector: Any,
    recognizer: Any,
    pixels: np.ndarray,
    detection_size: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    textlines, _, _ = await detector.detect(
        pixels,
        detection_size,
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        UNCLIP_RATIO,
        *_DETECTOR_FLAGS,
    )
    detector_elapsed = time.perf_counter() - started
    target_lines = [
        copy.deepcopy(line)
        for line in textlines
        if _intersects([int(value) for value in line.xyxy])
    ]
    started = time.perf_counter()
    recognized = await recognizer.recognize(
        pixels,
        target_lines,
        _OcrConfig(prob=MINIMUM_CONFIDENCE),
        _RECOGNIZER_FLAG,
    )
    recognizer_elapsed = time.perf_counter() - started
    recognized = [line for line in recognized if str(line.text).strip()]
    return {
        "detector_candidates_total": len(textlines),
        "target_detector_lines": [
            {
                "xyxy": [int(value) for value in line.xyxy],
                "score": float(line.prob),
            }
            for line in textlines
            if _intersects([int(value) for value in line.xyxy])
        ],
        "target_recognized_lines": [
            {
                "xyxy": [int(value) for value in line.xyxy],
                "probability": float(line.prob),
                "codepoints": len(str(line.text)),
                "middle_dot_codepoints": str(line.text).count("・"),
            }
            for line in recognized
        ],
        "detector_elapsed_seconds": detector_elapsed,
        "recognizer_elapsed_seconds": recognizer_elapsed,
    }


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
    components = _source_interpuncts(pixels)
    if len(components) != 7:
        raise ValueError(f"expected seven reviewed interpunct components, found {len(components)}")

    observations = []
    for detection_size in DETECTION_SIZES:
        pred, coordinate_ratio, forward_elapsed, effective = _forward(
            pixels,
            detection_size,
        )
        sampled = _sample_components(pred, coordinate_ratio, components)
        observations.append(
            {
                "detection_size": detection_size,
                "effective_detector_dimensions": effective,
                "coordinate_scale": coordinate_ratio,
                "raw_forward_elapsed_seconds": forward_elapsed,
                "interpuncts": sampled,
                "interpunct_threshold_crossings": sum(
                    item["crosses_text_threshold"] for item in sampled
                ),
                "target_representer_boxes": _representer_target_boxes(
                    pred,
                    coordinate_ratio,
                ),
                "production_target": await _production_target(
                    detector,
                    recognizer,
                    pixels,
                    detection_size,
                ),
            }
        )

    report = {
        "schema_version": 1,
        "privacy": (
            "No recognized manga text or source image bytes are serialized; only "
            "scores, geometry, codepoint counts and timing are recorded."
        ),
        "page9": {"sha256": digest, "width": 1414, "height": 2000},
        "thresholds": {
            "text_threshold": TEXT_THRESHOLD,
            "box_threshold": BOX_THRESHOLD,
            "unclip_ratio": UNCLIP_RATIO,
            "minimum_confidence": MINIMUM_CONFIDENCE,
        },
        "model_manifest_version": manifest.version,
        "upstream_commit": manifest.upstream_commit,
        "observations": observations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            [
                {
                    "detection_size": item["detection_size"],
                    "crossings": item["interpunct_threshold_crossings"],
                    "target_boxes": item["target_representer_boxes"],
                    "recognized": item["production_target"]["target_recognized_lines"],
                    "forward_seconds": round(item["raw_forward_elapsed_seconds"], 3),
                }
                for item in observations
            ],
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
