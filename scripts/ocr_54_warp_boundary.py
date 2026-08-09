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
PADS = (0, 1, 2, 3)
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


def _recognizer_space_crop(pad: int) -> CropFn:
    def crop(line: Quadrilateral, img: np.ndarray, direction: str, textheight: int) -> np.ndarray:
        if pad * 2 >= textheight:
            raise ValueError("recognizer padding must leave positive text height")

        [l1a, l1b, l2a, l2b] = [point.astype(np.float32) for point in line.structure]
        ratio = float(np.linalg.norm(l1b - l1a) / np.linalg.norm(l2b - l2a))
        inner_short = textheight - 2 * pad
        source = np.asarray(line.pts, dtype=np.float32).copy()
        image_height, image_width = img.shape[:2]
        source[:, 0] = np.clip(source[:, 0], 0, image_width - 1)
        source[:, 1] = np.clip(source[:, 1], 0, image_height - 1)

        if direction == "h":
            width = max(int(round(inner_short / ratio)), 2)
            height = textheight
            destination = np.asarray(
                [
                    [0, pad],
                    [width - 1, pad],
                    [width - 1, pad + inner_short - 1],
                    [0, pad + inner_short - 1],
                ],
                dtype=np.float32,
            )
        elif direction == "v":
            width = textheight
            height = max(int(round(inner_short * ratio)), 2)
            destination = np.asarray(
                [
                    [pad, 0],
                    [pad + inner_short - 1, 0],
                    [pad + inner_short - 1, height - 1],
                    [pad, height - 1],
                ],
                dtype=np.float32,
            )
        else:
            raise ValueError(f"unsupported direction: {direction}")

        matrix = cv2.getPerspectiveTransform(source, destination)
        region = cv2.warpPerspective(
            img,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        if direction == "v":
            region = cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return region

    crop.__name__ = f"recognizer_space_pad_{pad}"
    return crop


@contextmanager
def _patched_crop(fn: CropFn) -> Iterator[None]:
    original = Quadrilateral.get_transformed_region
    Quadrilateral.get_transformed_region = fn  # type: ignore[method-assign]
    try:
        yield
    finally:
        Quadrilateral.get_transformed_region = original  # type: ignore[method-assign]


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
    return pixels, [line for line in lines if _center_in_zone(line, effective_zone)]


async def _observe(
    *,
    page: str,
    pad: int,
    recognizer: MangaSenseiModel48pxOCR,
    pixels: np.ndarray,
    targets: list[Quadrilateral],
    config_type: type[Any],
) -> dict[str, Any]:
    # Disable #94 source-space expansion: this experiment expresses the contract
    # directly in the recognizer's normalized 48px coordinate system.
    recognizer._short_axis_context = 1.0
    zero = config_type(prob=0.0, ignore_bubble=0)
    prod = config_type(prob=0.2, ignore_bubble=0)
    crop_fn = _recognizer_space_crop(pad)
    with _patched_crop(crop_fn):
        zero_lines = await recognizer.recognize(
            pixels, copy.deepcopy(targets), zero, _RECOGNIZER_FLAG
        )
        prod_lines = await recognizer.recognize(
            pixels, copy.deepcopy(targets), prod, _RECOGNIZER_FLAG
        )
    probabilities = [float(line.prob) for line in zero_lines]
    print(
        "DEST_INSET "
        f"page={page} normalized_pad={pad} production_count={len(prod_lines)} "
        f"probabilities={[round(value, 6) for value in probabilities]}"
    )
    return {"production_count": len(prod_lines), "probabilities": probabilities}


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

    page171_pixels, page171_targets = await _detect_targets(engine, PAGE171, PAGE171_ZONE)
    page171_targets = [
        line for line in page171_targets if float(line.xyxy[3]) - float(line.xyxy[1]) >= 500
    ]
    if len(page171_targets) != 2:
        raise RuntimeError(f"page171 expected 2 long targets, got {len(page171_targets)}")

    recognizer = MangaSenseiModel48pxOCR(short_axis_context=1.0)
    MangaSenseiModel48pxOCR._MODEL_DIR = str(model_cache)
    await recognizer.load("cpu")

    pages = {
        "page9-scale-090": (page9_pixels, page9_targets),
        "page171": (page171_pixels, page171_targets),
    }
    payload: dict[str, Any] = {"normalization_height": TEXT_HEIGHT, "pages": {}}
    for page, (pixels, targets) in pages.items():
        page_record: dict[str, Any] = {
            "detector_xyxy": [[int(value) for value in line.xyxy] for line in targets],
            "pads": {},
        }
        for pad in PADS:
            page_record["pads"][str(pad)] = await _observe(
                page=page,
                pad=pad,
                recognizer=recognizer,
                pixels=pixels,
                targets=targets,
                config_type=config_type,
            )
        payload["pages"][page] = page_record

    (OUT / "destination-inset.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
