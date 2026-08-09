"""Trace page-201 false-positive lines under exclusive vs inclusive recognizer crops."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
)
from mangasensei.ocr.adapter.recognizer_48px import _RecognitionQuadrilateral

FIXTURE = (
    Path(__file__).parents[1]
    / "tests"
    / "fixtures"
    / "ocr"
    / "real_manga"
    / "black_jack"
    / "v01"
    / "black_jack_v01_pdf201.jpg"
)
TARGET_INDICES = tuple(range(7))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, default=Path("var/models"))
    return parser.parse_args()


def _geometry_key(line: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(line.pts))


def _recognized_map(lines: list[Any]) -> dict[tuple[tuple[int, int], ...], dict[str, Any]]:
    return {
        _geometry_key(line): {
            "text": str(line.text),
            "confidence": float(line.prob),
        }
        for line in lines
        if str(line.text).strip()
    }


def _textblock_text(region: Any) -> str:
    text = region.text
    if isinstance(text, list):
        return "".join(str(item) for item in text)
    return str(text)


def _serialize_merged(regions: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "text": _textblock_text(region),
            "confidence": float(region.prob),
            "xyxy": [int(value) for value in region.xyxy],
        }
        for region in regions
    ]


def _exclusive_region(
    self: _RecognitionQuadrilateral,
    img: np.ndarray,
    direction: str,
    textheight: int,
) -> np.ndarray:
    [l1a, l1b, l2a, l2b] = [point.astype(np.float32) for point in self.structure]
    vertical_vector = l1b - l1a
    horizontal_vector = l2b - l2a
    ratio = np.linalg.norm(vertical_vector) / np.linalg.norm(horizontal_vector)

    source = self.pts.astype(np.int64).copy()
    image_height, image_width = img.shape[:2]
    x1 = int(np.clip(source[:, 0].min(), 0, image_width))
    y1 = int(np.clip(source[:, 1].min(), 0, image_height))
    x2 = int(np.clip(source[:, 0].max(), 0, image_width))
    y2 = int(np.clip(source[:, 1].max(), 0, image_height))
    cropped = img[y1:y2, x1:x2]
    source[:, 0] -= x1
    source[:, 1] -= y1

    self.assigned_direction = direction
    if direction == "h":
        height = max(int(textheight), 2)
        width = max(int(round(textheight / ratio)), 2)
    elif direction == "v":
        width = max(int(textheight), 2)
        height = max(int(round(textheight * ratio)), 2)
    else:
        raise ValueError(f"unsupported direction: {direction}")
    destination = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix, _ = cv2.findHomography(source.astype(np.float32), destination, cv2.RANSAC, 5.0)
    if matrix is None:
        raise RuntimeError("exclusive diagnostic could not construct homography")
    region = cv2.warpPerspective(cropped, matrix, (width, height))
    if direction == "v":
        return cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return region


async def _recognize_variant(
    *,
    recognizer: Any,
    merge: Any,
    pixels: np.ndarray,
    textlines: list[Any],
    config: Any,
    inclusive: bool,
) -> dict[str, Any]:
    original = _RecognitionQuadrilateral.get_transformed_region
    if not inclusive:
        _RecognitionQuadrilateral.get_transformed_region = _exclusive_region
    try:
        full_input = copy.deepcopy(textlines)
        recognized = await recognizer.recognize(pixels, full_input, config, _RECOGNIZER_FLAG)
        recognized_by_geometry = _recognized_map(recognized)
        merged = await merge(recognized, pixels.shape[1], pixels.shape[0])

        targets = []
        for index in TARGET_INDICES:
            line = textlines[index]
            key = _geometry_key(line)
            isolated = await recognizer.recognize(
                pixels,
                [copy.deepcopy(line)],
                config,
                _RECOGNIZER_FLAG,
            )
            isolated_result = _recognized_map(isolated).get(key)
            targets.append(
                {
                    "index": index,
                    "xyxy": [int(value) for value in line.xyxy],
                    "direction": str(line.direction),
                    "font_size": float(line.font_size),
                    "detector_probability": float(line.prob),
                    "full_batch": recognized_by_geometry.get(key),
                    "isolated": isolated_result,
                }
            )
        return {
            "recognized_count": len(recognized),
            "targets": targets,
            "merged": _serialize_merged(merged),
        }
    finally:
        _RecognitionQuadrilateral.get_transformed_region = original


async def _run() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with Image.open(FIXTURE) as source:
        image = source.convert("RGB")
    pixels = np.asarray(image)

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, merge, _ = await engine._ensure_loaded()
    textlines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    payload = {
        "schema_version": 1,
        "fixture": str(FIXTURE.relative_to(Path(__file__).parents[1])),
        "detector_count": len(textlines),
        "exclusive": await _recognize_variant(
            recognizer=recognizer,
            merge=merge,
            pixels=pixels,
            textlines=textlines,
            config=engine._ocr_config,
            inclusive=False,
        ),
        "inclusive": await _recognize_variant(
            recognizer=recognizer,
            merge=merge,
            pixels=pixels,
            textlines=textlines,
            config=engine._ocr_config,
            inclusive=True,
        ),
    }
    (output / "p201-crop-regression-trace.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_P201_CROP_TRACE "
        f"detector={len(textlines)} "
        f"exclusive_recognized={payload['exclusive']['recognized_count']} "
        f"inclusive_recognized={payload['inclusive']['recognized_count']}"
    )


if __name__ == "__main__":
    asyncio.run(_run())
