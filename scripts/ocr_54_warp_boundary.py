from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

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
NORMALIZED_PADS = (0, 1, 2, 3, 4, 5, 6)
OUT = Path(os.environ.get("MANGASENSEI_OCR_WARP_ARTIFACT_DIR", "var/ocr-54-warp-boundary"))


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
    structure = [np.asarray(point, dtype=np.float32) for point in line.structure]
    l1a, l1b, l2a, l2b = structure
    vertical_vector = l1b - l1a
    horizontal_vector = l2b - l2a
    ratio = float(np.linalg.norm(vertical_vector) / np.linalg.norm(horizontal_vector))

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
        destination = np.asarray(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
        matrix, _ = cv2.findHomography(source, destination, cv2.RANSAC, 5.0)
        if matrix is None:
            raise RuntimeError("could not construct horizontal homography")
        return cv2.warpPerspective(cropped, matrix, (width, height))

    width = max(int(textheight), 2)
    height = max(int(round(textheight * ratio)), 2)
    destination = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix, _ = cv2.findHomography(source, destination, cv2.RANSAC, 5.0)
    if matrix is None:
        raise RuntimeError("could not construct vertical homography")
    region = cv2.warpPerspective(cropped, matrix, (width, height))
    return cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)


@contextmanager
def _patched_crop() -> Iterator[None]:
    original = Quadrilateral.get_transformed_region
    Quadrilateral.get_transformed_region = _crop_inclusive  # type: ignore[method-assign]
    try:
        yield
    finally:
        Quadrilateral.get_transformed_region = original  # type: ignore[method-assign]


def _factor_for_normalized_pad(pad: int) -> float:
    if pad == 0:
        return 1.0
    if pad * 2 >= TEXT_HEIGHT:
        raise ValueError("normalized pad must leave positive text width")
    return TEXT_HEIGHT / (TEXT_HEIGHT - 2 * pad)


async def _detect_targets(
    engine: MangaImageTranslatorEngine,
    source: Path,
    zone: tuple[int, int, int, int],
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
        effective_zone = _scale_zone(zone, scale)
    else:
        pixels = _decode_rgb(source.read_bytes())
        effective_zone = zone

    detector, _, _, _ = await engine._ensure_loaded()
    lines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    targets = [line for line in lines if _center_in_zone(line, effective_zone)]
    return pixels, targets


async def _sweep_page(
    *,
    label: str,
    pixels: np.ndarray,
    targets: list[Quadrilateral],
    config_type: type[Any],
    recognizer: MangaSenseiModel48pxOCR,
) -> dict[str, Any]:
    if not targets:
        raise RuntimeError(f"{label}: no detector targets selected")

    zero = config_type(prob=0.0, ignore_bubble=0)
    prod = config_type(prob=0.2, ignore_bubble=0)
    observations: dict[str, Any] = {}
    with _patched_crop():
        for pad in NORMALIZED_PADS:
            factor = _factor_for_normalized_pad(pad)
            recognizer._short_axis_context = factor
            zero_lines = await recognizer.recognize(
                pixels, copy.deepcopy(targets), zero, _RECOGNIZER_FLAG
            )
            prod_lines = await recognizer.recognize(
                pixels, copy.deepcopy(targets), prod, _RECOGNIZER_FLAG
            )
            record = {
                "normalized_pad_per_side": pad,
                "factor": factor,
                "zero_count": len(zero_lines),
                "probabilities": [float(line.prob) for line in zero_lines],
                "production_count": len(prod_lines),
            }
            observations[str(pad)] = record
            print(
                "PAD_SWEEP "
                f"page={label} normalized_pad={pad} factor={factor:.6f} "
                f"production_count={len(prod_lines)} "
                f"probabilities={[round(float(line.prob), 6) for line in zero_lines]}"
            )
    return {
        "detector_count": len(targets),
        "detector_xyxy": [[int(v) for v in line.xyxy] for line in targets],
        "observations": observations,
    }


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logging.getLogger("manga-translator.Model48pxOCR").setLevel(logging.WARNING)
    model_cache = Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models"))
    engine = MangaImageTranslatorEngine(model_cache=model_cache, device="cpu")
    config_type = type(engine._ocr_config)

    page9_pixels, page9_targets = await _detect_targets(
        engine, PAGE9, PAGE9_ZONE, scale=PAGE9_SCALE
    )
    if len(page9_targets) != 3:
        raise RuntimeError(f"page9 expected 3 targets, got {len(page9_targets)}")

    page171_pixels, page171_targets = await _detect_targets(
        engine, PAGE171, PAGE171_ZONE
    )
    page171_targets = [
        line
        for line in page171_targets
        if (float(line.xyxy[3]) - float(line.xyxy[1])) >= 500
    ]
    if len(page171_targets) != 2:
        raise RuntimeError(
            "page171 expected 2 long footnote detector targets, "
            f"got {len(page171_targets)}: {[list(map(int, line.xyxy)) for line in page171_targets]}"
        )

    recognizer = MangaSenseiModel48pxOCR(short_axis_context=1.0)
    MangaSenseiModel48pxOCR._MODEL_DIR = str(model_cache)
    await recognizer.load("cpu")

    payload = {
        "normalization_height": TEXT_HEIGHT,
        "page9_scale": PAGE9_SCALE,
        "pages": {
            "page9-scale-090": await _sweep_page(
                label="page9-scale-090",
                pixels=page9_pixels,
                targets=page9_targets,
                config_type=config_type,
                recognizer=recognizer,
            ),
            "page171": await _sweep_page(
                label="page171",
                pixels=page171_pixels,
                targets=page171_targets,
                config_type=config_type,
                recognizer=recognizer,
            ),
        },
    }
    (OUT / "padding-sweep.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
