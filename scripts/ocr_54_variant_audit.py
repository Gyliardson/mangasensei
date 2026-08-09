from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
)
from mangasensei.ocr.contracts import OcrImage
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)

FIXTURE_ROOT = Path("tests/fixtures/ocr/real_manga/black_jack")
PAGE9_PATH = FIXTURE_ROOT / "v01/black_jack_v01_pdf009.jpg"
OUTPUT_ROOT = Path(os.environ.get("MANGASENSEI_OCR_AUDIT_DIR", "var/ocr54-variant-audit"))
BASE_TARGET_ZONE = (130, 285, 455, 745)
EXPECTED_TARGET_TEXT = "医師国家試験に合格しなければいけない"
CONTEXT_FACTORS = (1.0, 1.04, 1.08, 1.12, 1.16, 1.20)


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    content: bytes
    media_type: str
    scale_x: float
    scale_y: float


def _encode(image: Image.Image, fmt: str, **kwargs: Any) -> bytes:
    out = io.BytesIO()
    image.save(out, format=fmt, **kwargs)
    return out.getvalue()


def _variants() -> list[Variant]:
    original = PAGE9_PATH.read_bytes()
    with Image.open(PAGE9_PATH) as opened:
        rgb = opened.convert("RGB")
        width, height = rgb.size
        variants = [Variant("original", original, "image/jpeg", 1.0, 1.0)]
        variants.append(Variant("png-lossless", _encode(rgb, "PNG", optimize=True), "image/png", 1.0, 1.0))
        for quality in (95, 90, 85, 75):
            variants.append(
                Variant(
                    f"jpeg-q{quality}",
                    _encode(rgb, "JPEG", quality=quality, optimize=True),
                    "image/jpeg",
                    1.0,
                    1.0,
                )
            )
        for scale in (0.9, 0.75, 0.5):
            resized = rgb.resize(
                (round(width * scale), round(height * scale)),
                Image.Resampling.LANCZOS,
            )
            variants.append(
                Variant(
                    f"scale-{scale:.2f}",
                    _encode(resized, "PNG", optimize=True),
                    "image/png",
                    resized.width / width,
                    resized.height / height,
                )
            )
    return variants


def _xyxy(value: Any) -> tuple[int, int, int, int]:
    return tuple(int(round(float(item))) for item in value.xyxy)  # type: ignore[return-value]


def _scaled_zone(variant: Variant) -> tuple[int, int, int, int]:
    x1, x2, y1, y2 = BASE_TARGET_ZONE
    return (
        round(x1 * variant.scale_x),
        round(x2 * variant.scale_x),
        round(y1 * variant.scale_y),
        round(y2 * variant.scale_y),
    )


def _center_in_zone(value: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = _xyxy(value)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def _line_record(index: int, line: Any) -> dict[str, Any]:
    return {
        "index": index,
        "xyxy": list(_xyxy(line)),
        "points": [[int(round(float(x))), int(round(float(y)))] for x, y in line.pts],
        "direction": str(line.direction),
        "font_size": float(line.font_size),
        "probability": float(line.prob),
        "text": str(getattr(line, "text", "")),
    }


def _block_record(index: int, block: Any) -> dict[str, Any]:
    return {
        "index": index,
        "xyxy": list(_xyxy(block)),
        "probability": float(block.prob),
        "text": str(block.text),
    }


def _expanded_line(line: Any, factor: float, width: int, height: int) -> Quadrilateral:
    pts = np.asarray(line.pts, dtype=np.float32).copy()
    center = pts.mean(axis=0)
    if str(line.direction) == "v":
        pts[:, 0] = center[0] + (pts[:, 0] - center[0]) * factor
    else:
        pts[:, 1] = center[1] + (pts[:, 1] - center[1]) * factor
    pts[:, 0] = np.clip(pts[:, 0], 0, width)
    pts[:, 1] = np.clip(pts[:, 1], 0, height)
    return Quadrilateral(
        pts,
        "",
        float(line.prob),
        int(line.fg_r),
        int(line.fg_g),
        int(line.fg_b),
        int(line.bg_r),
        int(line.bg_g),
        int(line.bg_b),
    )


def _draw_boxes(
    content: bytes,
    destination: Path,
    boxes: list[tuple[int, int, int, int]],
    target_zone: tuple[int, int, int, int],
) -> None:
    with Image.open(io.BytesIO(content)) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    line_width = max(2, round(max(image.size) / 700))
    for index, box in enumerate(boxes):
        draw.rectangle(box, outline=(220, 20, 60), width=line_width)
        x1, y1, _, _ = box
        tx = max(0, min(x1, image.width - 18))
        ty = max(0, y1 - 13)
        draw.rectangle((tx, ty, tx + 18, ty + 13), fill=(255, 255, 255))
        draw.text((tx + 2, ty + 1), str(index), fill=(0, 0, 0), font=font)
    min_x, max_x, min_y, max_y = target_zone
    draw.rectangle(
        (min_x, min_y, max_x, max_y),
        outline=(30, 110, 220),
        width=max(line_width, 3),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, quality=92)


async def _analyze_variant(engine: MangaImageTranslatorEngine, variant: Variant) -> dict[str, Any]:
    detector, recognizer, merge, _ = await engine._ensure_loaded()
    pixels = _decode_rgb(variant.content)
    height, width = pixels.shape[:2]
    zone = _scaled_zone(variant)

    detected, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    detected_snapshot = copy.deepcopy(detected)
    target_detector = [line for line in detected_snapshot if _center_in_zone(line, zone)]

    recognized = await recognizer.recognize(
        pixels,
        copy.deepcopy(detected_snapshot),
        engine._ocr_config,
        _RECOGNIZER_FLAG,
    )
    recognized = [line for line in recognized if str(line.text).strip()]
    merged = await merge(copy.deepcopy(recognized), width, height)
    target_merged = [block for block in merged if _center_in_zone(block, zone)]

    result = await engine.analyze(
        OcrImage(
            content=variant.content,
            sha256=hashlib.sha256(variant.content).hexdigest(),
            media_type=variant.media_type,
            dimensions=PageDimensions(width=width, height=height),
        )
    )
    final_target = [
        region
        for region in result.regions
        if _center_in_zone(
            type(
                "Box",
                (),
                {
                    "xyxy": (
                        region.bbox.x,
                        region.bbox.y,
                        region.bbox.x + region.bbox.width,
                        region.bbox.y + region.bbox.height,
                    )
                },
            )(),
            zone,
        )
    ]

    target_recognized = [line for line in recognized if _center_in_zone(line, zone)]
    target_context: list[dict[str, Any]] = []
    if target_detector:
        rightmost = max(target_detector, key=lambda line: (float(line.xyxy[0]) + float(line.xyxy[2])) / 2)
        for factor in CONTEXT_FACTORS:
            expanded = _expanded_line(rightmost, factor, width, height)
            output = await recognizer.recognize(
                pixels,
                [expanded],
                engine._ocr_config,
                _RECOGNIZER_FLAG,
            )
            target_context.append(
                {
                    "factor": factor,
                    "input_xyxy": list(_xyxy(expanded)),
                    "recognized": [_line_record(index, line) for index, line in enumerate(output)],
                }
            )

    _draw_boxes(
        variant.content,
        OUTPUT_ROOT / "variants" / f"{variant.name}-detector.jpg",
        [_xyxy(line) for line in detected_snapshot],
        zone,
    )
    _draw_boxes(
        variant.content,
        OUTPUT_ROOT / "variants" / f"{variant.name}-final.jpg",
        [
            (
                region.bbox.x,
                region.bbox.y,
                region.bbox.x + region.bbox.width,
                region.bbox.y + region.bbox.height,
            )
            for region in result.regions
        ],
        zone,
    )

    return {
        "name": variant.name,
        "sha256": hashlib.sha256(variant.content).hexdigest(),
        "media_type": variant.media_type,
        "size": [width, height],
        "scale": [variant.scale_x, variant.scale_y],
        "target_zone": list(zone),
        "detector_count": len(detected_snapshot),
        "target_detector": [_line_record(index, line) for index, line in enumerate(target_detector)],
        "recognized_count": len(recognized),
        "target_recognized": [_line_record(index, line) for index, line in enumerate(target_recognized)],
        "merged_count": len(merged),
        "target_merged": [_block_record(index, block) for index, block in enumerate(target_merged)],
        "final_region_count": len(result.regions),
        "final_target": [
            {
                "bbox": [
                    region.bbox.x,
                    region.bbox.y,
                    region.bbox.width,
                    region.bbox.height,
                ],
                "confidence": float(region.confidence),
                "text": region.japanese_text,
            }
            for region in final_target
        ],
        "expected_target_present": any(EXPECTED_TARGET_TEXT in region.japanese_text for region in final_target),
        "rightmost_context_sweep": target_context,
    }


async def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    engine = MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )
    observations: list[dict[str, Any]] = []
    for variant in _variants():
        observation = await _analyze_variant(engine, variant)
        observations.append(observation)
        target_probs = [round(float(line["probability"]), 4) for line in observation["target_recognized"]]
        print(
            "VARIANT "
            f"name={variant.name} detector={observation['detector_count']} "
            f"target_detector={len(observation['target_detector'])} "
            f"target_recognized={len(observation['target_recognized'])} "
            f"target_probs={target_probs} final_regions={observation['final_region_count']}"
        )
    payload = {
        "expected_target_text": EXPECTED_TARGET_TEXT,
        "context_factors": list(CONTEXT_FACTORS),
        "variants": observations,
    }
    (OUTPUT_ROOT / "variants.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
