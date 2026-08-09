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
from mangasensei.ocr.adapter.recognizer_48px import MangaSenseiModel48pxOCR
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)

ROOT = Path("tests/fixtures/ocr/real_manga/black_jack/v01")
PAGE9 = ROOT / "black_jack_v01_pdf009.jpg"
PAGE171 = ROOT / "black_jack_v01_pdf171.jpg"
PAGE9_ZONE = (130, 285, 455, 745)
PAGE171_ZONE = (70, 180, 650, 1750)
PAGE9_SCALE = 0.9
TEXT_HEIGHT = 48
FIRST_CONV_RADIUS = 3
ARCH_FACTOR = TEXT_HEIGHT / (TEXT_HEIGHT - 2 * FIRST_CONV_RADIUS)
FACTORS = (1.0, ARCH_FACTOR, 1.15, 1.16)
OUT = Path(os.environ.get("MANGASENSEI_OCR_WARP_ARTIFACT_DIR", "var/ocr-54-warp-boundary"))
CropFn = Callable[[Quadrilateral, np.ndarray, str, int], np.ndarray]


def _center_in_zone(line: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = (float(v) for v in line.xyxy)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def _scale_zone(zone: tuple[int, int, int, int], factor: float) -> tuple[int, int, int, int]:
    return tuple(round(value * factor) for value in zone)  # type: ignore[return-value]


def _crop_inclusive(
    line: Quadrilateral,
    img: np.ndarray,
    direction: str,
    textheight: int,
) -> np.ndarray:
    [l1a, l1b, l2a, l2b] = [point.astype(np.float32) for point in line.structure]
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
    elif direction == "v":
        width = max(int(textheight), 2)
        height = max(int(round(textheight * ratio)), 2)
    else:
        raise ValueError(f"unsupported direction: {direction}")

    destination = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(source, destination)
    region = cv2.warpPerspective(cropped, matrix, (width, height))
    if direction == "v":
        region = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return region


@contextmanager
def _patched_crop(fn: CropFn) -> Iterator[None]:
    original = Quadrilateral.get_transformed_region
    Quadrilateral.get_transformed_region = fn  # type: ignore[method-assign]
    try:
        yield
    finally:
        Quadrilateral.get_transformed_region = original  # type: ignore[method-assign]


async def _load_page(
    engine: MangaImageTranslatorEngine,
    source: Path,
    *,
    scale: float = 1.0,
) -> tuple[np.ndarray, list[Quadrilateral]]:
    if scale != 1.0:
        with Image.open(source) as opened:
            resized = opened.convert("RGB").resize(
                (round(opened.width * scale), round(opened.height * scale)),
                Image.Resampling.LANCZOS,
            )
        encoded = OUT / f"{source.stem}-scale-{round(scale * 100):03d}.png"
        resized.save(encoded, format="PNG")
        pixels = _decode_rgb(encoded.read_bytes())
    else:
        pixels = _decode_rgb(source.read_bytes())

    detector, _, _, _ = await engine._ensure_loaded()
    lines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    return pixels, lines


def _record(line: Quadrilateral) -> dict[str, Any]:
    return {
        "xyxy": [int(value) for value in line.xyxy],
        "probability": float(line.prob),
        "text": str(line.text),
        "text_length": len(str(line.text)),
    }


async def _observe_factor(
    recognizer: MangaSenseiModel48pxOCR,
    pixels: np.ndarray,
    all_lines: list[Quadrilateral],
    target_zone: tuple[int, int, int, int],
    config_type: type[Any],
    factor: float,
    label: str,
) -> dict[str, Any]:
    recognizer._short_axis_context = factor
    zero = config_type(prob=0.0, ignore_bubble=0)
    prod = config_type(prob=0.2, ignore_bubble=0)
    with _patched_crop(_crop_inclusive):
        zero_lines = await recognizer.recognize(
            pixels, copy.deepcopy(all_lines), zero, _RECOGNIZER_FLAG
        )
        prod_lines = await recognizer.recognize(
            pixels, copy.deepcopy(all_lines), prod, _RECOGNIZER_FLAG
        )
    zero_targets = [line for line in zero_lines if _center_in_zone(line, target_zone)]
    prod_targets = [line for line in prod_lines if _center_in_zone(line, target_zone)]
    print(
        "SOURCE_CONTEXT "
        f"page={label} factor={factor:.9f} "
        f"production_target_count={len(prod_targets)} "
        f"target_lengths={[len(str(line.text)) for line in prod_targets]}"
    )
    return {
        "factor": factor,
        "zero_targets": [_record(line) for line in zero_targets],
        "production_targets": [_record(line) for line in prod_targets],
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logging.getLogger("manga-translator.Model48pxOCR").setLevel(logging.WARNING)
    model_cache = Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models"))
    engine = MangaImageTranslatorEngine(model_cache=model_cache, device="cpu")
    config_type = type(engine._ocr_config)

    page9_pixels, page9_lines = await _load_page(engine, PAGE9, scale=PAGE9_SCALE)
    page171_pixels, page171_lines = await _load_page(engine, PAGE171)
    page9_zone = _scale_zone(PAGE9_ZONE, PAGE9_SCALE)

    recognizer = MangaSenseiModel48pxOCR(short_axis_context=1.0)
    MangaSenseiModel48pxOCR._MODEL_DIR = str(model_cache)
    await recognizer.load("cpu")

    payload: dict[str, Any] = {
        "normalization_height": TEXT_HEIGHT,
        "first_conv_radius": FIRST_CONV_RADIUS,
        "architecture_factor": ARCH_FACTOR,
        "pages": {},
    }
    for label, pixels, lines, zone in (
        ("page9-scale-090", page9_pixels, page9_lines, page9_zone),
        ("page171", page171_pixels, page171_lines, PAGE171_ZONE),
    ):
        records = {}
        for factor in FACTORS:
            records[f"{factor:.9f}"] = await _observe_factor(
                recognizer,
                pixels,
                lines,
                zone,
                config_type,
                factor,
                label,
            )
        payload["pages"][label] = records

    (OUT / "source-context-full-batch.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
