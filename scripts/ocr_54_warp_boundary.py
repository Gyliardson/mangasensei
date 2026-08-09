from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

import cv2
import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
)
from mangasensei.ocr.adapter.recognizer_48px import (
    _expand_short_axis,
    MangaSenseiModel48pxOCR,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)

ROOT = Path("tests/fixtures/ocr/real_manga/black_jack/v01")
PAGE171 = ROOT / "black_jack_v01_pdf171.jpg"
PAGE171_ZONE = (70, 180, 650, 1750)
TEXT_HEIGHT = 48
PAD = 2
PAD_FACTOR = TEXT_HEIGHT / (TEXT_HEIGHT - 2 * PAD)
OUT = Path(os.environ.get("MANGASENSEI_OCR_WARP_ARTIFACT_DIR", "var/ocr-54-warp-boundary"))
CropFn = Callable[[Quadrilateral, np.ndarray, str, int], np.ndarray]


def _center_in_zone(line: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = (float(v) for v in line.xyxy)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def _crop_inclusive(
    line: Quadrilateral,
    img: np.ndarray,
    direction: str,
    textheight: int,
) -> np.ndarray:
    structure = [np.asarray(point, dtype=np.float32) for point in line.structure]
    l1a, l1b, l2a, l2b = structure
    ratio = float(np.linalg.norm(l1b - l1a) / np.linalg.norm(l2b - l2a))
    source_points = np.asarray(line.pts, dtype=np.int64).copy()
    image_height, image_width = img.shape[:2]
    source_points[:, 0] = np.clip(source_points[:, 0], 0, image_width - 1)
    source_points[:, 1] = np.clip(source_points[:, 1], 0, image_height - 1)
    x1 = int(source_points[:, 0].min())
    y1 = int(source_points[:, 1].min())
    x2 = int(source_points[:, 0].max())
    y2 = int(source_points[:, 1].max())
    cropped = img[y1 : y2 + 1, x1 : x2 + 1]
    source_points[:, 0] -= x1
    source_points[:, 1] -= y1
    source = source_points.astype(np.float32)

    if direction == "h":
        height = max(int(textheight), 2)
        width = max(int(round(textheight / ratio)), 2)
    else:
        width = max(int(textheight), 2)
        height = max(int(round(textheight * ratio)), 2)
    destination = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix, _ = cv2.findHomography(source, destination, cv2.RANSAC, 5.0)
    if matrix is None:
        raise RuntimeError("could not construct homography")
    region = cv2.warpPerspective(cropped, matrix, (width, height))
    if direction == "v":
        region = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return region


def _postpad(line: Quadrilateral, img: np.ndarray, direction: str, textheight: int, border: int, value: int = 0) -> np.ndarray:
    crop = _crop_inclusive(line, img, direction, textheight)
    inner_height = textheight - 2 * PAD
    inner_width = max(1, round(crop.shape[1] * inner_height / textheight))
    resized = cv2.resize(crop, (inner_width, inner_height), interpolation=cv2.INTER_LINEAR)
    if border == cv2.BORDER_CONSTANT:
        return cv2.copyMakeBorder(resized, PAD, PAD, 0, 0, border, value=(value, value, value))
    return cv2.copyMakeBorder(resized, PAD, PAD, 0, 0, border)


def _postpad_replicate(line: Quadrilateral, img: np.ndarray, direction: str, textheight: int) -> np.ndarray:
    return _postpad(line, img, direction, textheight, cv2.BORDER_REPLICATE)


def _postpad_white(line: Quadrilateral, img: np.ndarray, direction: str, textheight: int) -> np.ndarray:
    return _postpad(line, img, direction, textheight, cv2.BORDER_CONSTANT, 255)


@contextmanager
def _patched_crop(fn: CropFn) -> Iterator[None]:
    original = Quadrilateral.get_transformed_region
    Quadrilateral.get_transformed_region = fn  # type: ignore[method-assign]
    try:
        yield
    finally:
        Quadrilateral.get_transformed_region = original  # type: ignore[method-assign]


async def _observe(
    recognizer: MangaSenseiModel48pxOCR,
    pixels: np.ndarray,
    targets: list[Quadrilateral],
    config_type: type[Any],
    *,
    crop_fn: CropFn,
    context_factor: float,
    label: str,
) -> dict[str, Any]:
    recognizer._short_axis_context = context_factor
    zero = config_type(prob=0.0, ignore_bubble=0)
    prod = config_type(prob=0.2, ignore_bubble=0)
    with _patched_crop(crop_fn):
        zero_lines = await recognizer.recognize(
            pixels, copy.deepcopy(targets), zero, _RECOGNIZER_FLAG
        )
        prod_lines = await recognizer.recognize(
            pixels, copy.deepcopy(targets), prod, _RECOGNIZER_FLAG
        )
    record = {
        "production_count": len(prod_lines),
        "probabilities": [float(line.prob) for line in zero_lines],
    }
    print(
        "CONTEXT_KIND "
        f"variant={label} production_count={len(prod_lines)} "
        f"probabilities={[round(float(line.prob), 6) for line in zero_lines]}"
    )
    return record


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logging.getLogger("manga-translator.Model48pxOCR").setLevel(logging.WARNING)
    model_cache = Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models"))
    engine = MangaImageTranslatorEngine(model_cache=model_cache, device="cpu")
    detector, _, _, _ = await engine._ensure_loaded()
    pixels = _decode_rgb(PAGE171.read_bytes())
    lines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    targets = [
        line
        for line in lines
        if _center_in_zone(line, PAGE171_ZONE)
        and float(line.xyxy[3]) - float(line.xyxy[1]) >= 500
    ]
    if len(targets) != 2:
        raise RuntimeError(f"page171 expected 2 long targets, got {len(targets)}")

    recognizer = MangaSenseiModel48pxOCR(short_axis_context=1.0)
    MangaSenseiModel48pxOCR._MODEL_DIR = str(model_cache)
    await recognizer.load("cpu")
    config_type = type(engine._ocr_config)

    observations = {
        "exact_source": await _observe(
            recognizer, pixels, targets, config_type,
            crop_fn=_crop_inclusive, context_factor=1.0, label="exact_source"
        ),
        "postpad_replicate": await _observe(
            recognizer, pixels, targets, config_type,
            crop_fn=_postpad_replicate, context_factor=1.0, label="postpad_replicate"
        ),
        "postpad_white": await _observe(
            recognizer, pixels, targets, config_type,
            crop_fn=_postpad_white, context_factor=1.0, label="postpad_white"
        ),
        "source_context_pad2": await _observe(
            recognizer, pixels, targets, config_type,
            crop_fn=_crop_inclusive, context_factor=PAD_FACTOR, label="source_context_pad2"
        ),
    }

    # Save the two long detector crops with and without real source context for manual review.
    with _patched_crop(_crop_inclusive):
        for index, target in enumerate(targets):
            exact = target.get_transformed_region(pixels, "v", TEXT_HEIGHT)
            expanded = _expand_short_axis(
                target,
                factor=PAD_FACTOR,
                image_width=pixels.shape[1],
                image_height=pixels.shape[0],
            )
            context = expanded.get_transformed_region(pixels, "v", TEXT_HEIGHT)
            cv2.imwrite(str(OUT / f"page171-target-{index}-exact.png"), cv2.cvtColor(exact, cv2.COLOR_RGB2BGR))
            cv2.imwrite(str(OUT / f"page171-target-{index}-source-pad2.png"), cv2.cvtColor(context, cv2.COLOR_RGB2BGR))

    payload = {
        "normalized_pad_per_side": PAD,
        "context_factor": PAD_FACTOR,
        "detector_xyxy": [[int(v) for v in line.xyxy] for line in targets],
        "observations": observations,
    }
    (OUT / "context-kind.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
