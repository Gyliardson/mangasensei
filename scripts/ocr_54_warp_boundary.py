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

FIXTURE = Path(
    "tests/fixtures/ocr/real_manga/black_jack/v01/black_jack_v01_pdf009.jpg"
)
TARGET_ZONE = (130, 285, 455, 745)
SCALE = 0.9
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
    *,
    border_mode: int,
) -> np.ndarray:
    structure = [np.asarray(a, dtype=np.float32) for a in line.structure]
    l1a, l1b, l2a, l2b = structure
    v_vec = l1b - l1a
    h_vec = l2b - l2a
    ratio = float(np.linalg.norm(v_vec) / np.linalg.norm(h_vec))

    src_pts = np.asarray(line.pts, dtype=np.int64).copy()
    im_h, im_w = img.shape[:2]
    src_pts[:, 0] = np.clip(src_pts[:, 0], 0, im_w - 1)
    src_pts[:, 1] = np.clip(src_pts[:, 1], 0, im_h - 1)

    x1 = int(src_pts[:, 0].min())
    y1 = int(src_pts[:, 1].min())
    x2 = int(src_pts[:, 0].max())
    y2 = int(src_pts[:, 1].max())
    cropped = img[y1 : y2 + 1, x1 : x2 + 1]
    src_pts[:, 0] -= x1
    src_pts[:, 1] -= y1
    src = src_pts.astype(np.float32)

    if direction == "h":
        h = max(int(textheight), 2)
        w = max(int(round(textheight / ratio)), 2)
        dst = np.asarray([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
        matrix, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        return cv2.warpPerspective(cropped, matrix, (w, h), borderMode=border_mode)

    w = max(int(textheight), 2)
    h = max(int(round(textheight * ratio)), 2)
    dst = np.asarray([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    matrix, _ = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    region = cv2.warpPerspective(cropped, matrix, (w, h), borderMode=border_mode)
    return cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _inclusive_constant(line: Quadrilateral, img: np.ndarray, direction: str, textheight: int) -> np.ndarray:
    return _crop_inclusive(line, img, direction, textheight, border_mode=cv2.BORDER_CONSTANT)


def _inclusive_replicate(line: Quadrilateral, img: np.ndarray, direction: str, textheight: int) -> np.ndarray:
    return _crop_inclusive(line, img, direction, textheight, border_mode=cv2.BORDER_REPLICATE)


@contextmanager
def _patched_crop(fn: CropFn | None) -> Iterator[None]:
    original = Quadrilateral.get_transformed_region
    if fn is not None:
        Quadrilateral.get_transformed_region = fn  # type: ignore[method-assign]
    try:
        yield
    finally:
        Quadrilateral.get_transformed_region = original  # type: ignore[method-assign]


async def _recognizer(model_cache: Path, context: float) -> MangaSenseiModel48pxOCR:
    recognizer = MangaSenseiModel48pxOCR(short_axis_context=context)
    MangaSenseiModel48pxOCR._MODEL_DIR = str(model_cache)
    await recognizer.load("cpu")
    return recognizer


async def _observe(
    recognizer: MangaSenseiModel48pxOCR,
    pixels: np.ndarray,
    target_lines: list[Quadrilateral],
    config_type: type[Any],
    *,
    crop_fn: CropFn | None,
    label: str,
) -> dict[str, Any]:
    zero = config_type(prob=0.0, ignore_bubble=0)
    prod = config_type(prob=0.2, ignore_bubble=0)
    with _patched_crop(crop_fn):
        zero_lines = await recognizer.recognize(
            pixels, copy.deepcopy(target_lines), zero, _RECOGNIZER_FLAG
        )
        prod_lines = await recognizer.recognize(
            pixels, copy.deepcopy(target_lines), prod, _RECOGNIZER_FLAG
        )

        crop_records: list[dict[str, Any]] = []
        generated = list(recognizer._generate_text_direction(copy.deepcopy(target_lines)))
        for index, (line, direction) in enumerate(generated):
            crop = line.get_transformed_region(pixels, direction, 48)
            cv2.imwrite(str(OUT / f"{label}-{index}.png"), cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            edge = np.concatenate(
                [
                    crop[:, :1].reshape(-1, 3),
                    crop[:, -1:].reshape(-1, 3),
                    crop[:1, :].reshape(-1, 3),
                    crop[-1:, :].reshape(-1, 3),
                ],
                axis=0,
            )
            crop_records.append(
                {
                    "index": index,
                    "shape": list(crop.shape),
                    "edge_mean": float(edge.mean()),
                    "edge_dark_fraction": float((edge < 32).mean()),
                }
            )

    return {
        "zero_count": len(zero_lines),
        "zero_probabilities": [float(line.prob) for line in zero_lines],
        "production_count": len(prod_lines),
        "production_probabilities": [float(line.prob) for line in prod_lines],
        "crops": crop_records,
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logging.getLogger("manga-translator.Model48pxOCR").setLevel(logging.WARNING)

    with Image.open(FIXTURE) as source:
        resized = source.convert("RGB").resize(
            (round(source.width * SCALE), round(source.height * SCALE)),
            Image.Resampling.LANCZOS,
        )
    encoded = Path(OUT / "page9-scale-090.png")
    resized.save(encoded, format="PNG")
    pixels = _decode_rgb(encoded.read_bytes())

    engine = MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )
    detector, _, _, _ = await engine._ensure_loaded()
    textlines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    target = [line for line in textlines if _center_in_zone(line, _scale_zone(TARGET_ZONE, SCALE))]
    if len(target) != 3:
        raise RuntimeError(f"expected 3 target detector lines, got {len(target)}")

    model_cache = Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models"))
    no_context = await _recognizer(model_cache, 1.0)
    current_context = await _recognizer(model_cache, 1.16)
    config_type = type(engine._ocr_config)

    observations = {
        "upstream_tight": await _observe(
            no_context, pixels, target, config_type, crop_fn=None, label="upstream-tight"
        ),
        "inclusive_constant": await _observe(
            no_context,
            pixels,
            target,
            config_type,
            crop_fn=_inclusive_constant,
            label="inclusive-constant",
        ),
        "inclusive_replicate": await _observe(
            no_context,
            pixels,
            target,
            config_type,
            crop_fn=_inclusive_replicate,
            label="inclusive-replicate",
        ),
        "current_1_16": await _observe(
            current_context, pixels, target, config_type, crop_fn=None, label="current-1-16"
        ),
    }

    payload = {
        "detector_target_count": len(target),
        "detector_xyxy": [[int(v) for v in line.xyxy] for line in target],
        "observations": observations,
    }
    (OUT / "results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for name, value in observations.items():
        print(
            "WARP_BOUNDARY "
            f"variant={name} production_count={value['production_count']} "
            f"probabilities={[round(p, 6) for p in value['zero_probabilities']]}"
        )


if __name__ == "__main__":
    asyncio.run(main())
