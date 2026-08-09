from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
)
from mangasensei.ocr.contracts import OcrImage

FIXTURE_ROOT = Path("tests/fixtures/ocr/real_manga/black_jack")
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
OUTPUT_ROOT = Path(os.environ.get("MANGASENSEI_OCR_AUDIT_DIR", "var/ocr54-audit"))
PAGE9_PATH = "v01/black_jack_v01_pdf009.jpg"
PAGE9_OVERLAY3_ZONE = (130, 285, 455, 745)


def _xyxy(value: Any) -> tuple[int, int, int, int]:
    return tuple(int(round(float(item))) for item in value.xyxy)  # type: ignore[return-value]


def _center_in_zone(value: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = _xyxy(value)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def _line_record(index: int, line: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "index": index,
        "xyxy": list(_xyxy(line)),
        "points": [[int(round(float(x))), int(round(float(y)))] for x, y in line.pts],
        "direction": str(line.direction),
        "font_size": float(line.font_size),
        "probability": float(line.prob),
        "text": str(getattr(line, "text", "")),
    }
    return record


def _block_record(index: int, block: Any) -> dict[str, Any]:
    return {
        "index": index,
        "xyxy": list(_xyxy(block)),
        "probability": float(block.prob),
        "angle": float(block.angle),
        "text": str(block.text),
        "line_boxes": [
            [
                int(round(float(point[0]))),
                int(round(float(point[1]))),
            ]
            for line in block.lines
            for point in line
        ],
    }


def _draw_boxes(
    source: Path,
    destination: Path,
    boxes: list[tuple[int, int, int, int]],
    *,
    target_zone: tuple[int, int, int, int] | None = None,
) -> None:
    with Image.open(source) as opened:
        image = opened.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    line_width = max(2, round(max(image.size) / 700))

    for index, (x1, y1, x2, y2) in enumerate(boxes):
        draw.rectangle((x1, y1, x2, y2), outline=(220, 20, 60), width=line_width)
        label = str(index)
        tx = max(0, min(x1, image.width - 18))
        ty = max(0, y1 - 13)
        draw.rectangle((tx, ty, tx + 18, ty + 13), fill=(255, 255, 255))
        draw.text((tx + 2, ty + 1), label, fill=(0, 0, 0), font=font)

    if target_zone is not None:
        min_x, max_x, min_y, max_y = target_zone
        draw.rectangle(
            (min_x, min_y, max_x, max_y),
            outline=(30, 110, 220),
            width=max(line_width, 3),
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, quality=92)


def _region_record(index: int, region: Any) -> dict[str, Any]:
    return {
        "index": index,
        "bbox": [region.bbox.x, region.bbox.y, region.bbox.width, region.bbox.height],
        "xyxy": [
            region.bbox.x,
            region.bbox.y,
            region.bbox.x + region.bbox.width,
            region.bbox.y + region.bbox.height,
        ],
        "confidence": float(region.confidence),
        "text": region.japanese_text,
        "reading_order": region.reading_order,
    }


async def _analyze_page9_stages(engine: MangaImageTranslatorEngine) -> dict[str, Any]:
    detector, recognizer, merge, _ = await engine._ensure_loaded()
    source = FIXTURE_ROOT / PAGE9_PATH
    content = source.read_bytes()
    pixels = _decode_rgb(content)
    with Image.open(source) as opened:
        width, height = opened.size

    detected, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    detected_snapshot = copy.deepcopy(detected)
    target_detector_indices = [
        index for index, line in enumerate(detected_snapshot) if _center_in_zone(line, PAGE9_OVERLAY3_ZONE)
    ]

    recognized = await recognizer.recognize(
        pixels,
        copy.deepcopy(detected_snapshot),
        engine._ocr_config,
        _RECOGNIZER_FLAG,
    )
    recognized = [line for line in recognized if str(line.text).strip()]
    merged = await merge(copy.deepcopy(recognized), width, height)

    zero_config = type(engine._ocr_config)(prob=0.0, ignore_bubble=engine._ocr_config.ignore_bubble)
    target_detector = [copy.deepcopy(detected_snapshot[index]) for index in target_detector_indices]
    target_zero = await recognizer.recognize(
        pixels,
        copy.deepcopy(target_detector),
        zero_config,
        _RECOGNIZER_FLAG,
    )
    target_prod = await recognizer.recognize(
        pixels,
        copy.deepcopy(target_detector),
        engine._ocr_config,
        _RECOGNIZER_FLAG,
    )

    individual_runs: list[dict[str, Any]] = []
    for local_index, target in enumerate(target_detector):
        zero = await recognizer.recognize(
            pixels,
            [copy.deepcopy(target)],
            zero_config,
            _RECOGNIZER_FLAG,
        )
        prod = await recognizer.recognize(
            pixels,
            [copy.deepcopy(target)],
            engine._ocr_config,
            _RECOGNIZER_FLAG,
        )
        individual_runs.append(
            {
                "detector_index": target_detector_indices[local_index],
                "detector": _line_record(target_detector_indices[local_index], target),
                "zero_threshold": [_line_record(index, line) for index, line in enumerate(zero)],
                "production_threshold": [_line_record(index, line) for index, line in enumerate(prod)],
            }
        )

    result = await engine.analyze(
        OcrImage(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="image/jpeg",
            dimensions=PageDimensions(width=width, height=height),
        )
    )

    _draw_boxes(
        source,
        OUTPUT_ROOT / "page9-detector.jpg",
        [_xyxy(line) for line in detected_snapshot],
        target_zone=PAGE9_OVERLAY3_ZONE,
    )
    _draw_boxes(
        source,
        OUTPUT_ROOT / "page9-recognized.jpg",
        [_xyxy(line) for line in recognized],
        target_zone=PAGE9_OVERLAY3_ZONE,
    )
    _draw_boxes(
        source,
        OUTPUT_ROOT / "page9-merged.jpg",
        [_xyxy(block) for block in merged],
        target_zone=PAGE9_OVERLAY3_ZONE,
    )
    _draw_boxes(
        source,
        OUTPUT_ROOT / "page9-final.jpg",
        [tuple(_region_record(index, region)["xyxy"]) for index, region in enumerate(result.regions)],
        target_zone=PAGE9_OVERLAY3_ZONE,
    )

    return {
        "source": PAGE9_PATH,
        "size": [width, height],
        "overlay3_zone": list(PAGE9_OVERLAY3_ZONE),
        "detector_count": len(detected_snapshot),
        "target_detector_indices": target_detector_indices,
        "detector": [_line_record(index, line) for index, line in enumerate(detected_snapshot)],
        "recognized_count": len(recognized),
        "recognized": [_line_record(index, line) for index, line in enumerate(recognized)],
        "merged_count": len(merged),
        "merged": [_block_record(index, block) for index, block in enumerate(merged)],
        "target_narrow_zero_threshold": [
            _line_record(index, line) for index, line in enumerate(target_zero)
        ],
        "target_narrow_production_threshold": [
            _line_record(index, line) for index, line in enumerate(target_prod)
        ],
        "target_individual_runs": individual_runs,
        "final_regions": [_region_record(index, region) for index, region in enumerate(result.regions)],
    }


async def _audit_full_corpus(engine: MangaImageTranslatorEngine) -> list[dict[str, Any]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    audit: list[dict[str, Any]] = []
    for entry in manifest["fixtures"]:
        relative_path = str(entry["file"])
        source = FIXTURE_ROOT / relative_path
        content = source.read_bytes()
        with Image.open(source) as opened:
            width, height = opened.size
        result = await engine.analyze(
            OcrImage(
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                media_type="image/jpeg",
                dimensions=PageDimensions(width=width, height=height),
            )
        )
        regions = [_region_record(index, region) for index, region in enumerate(result.regions)]
        _draw_boxes(
            source,
            OUTPUT_ROOT / "corpus" / f"{Path(relative_path).stem}-final.jpg",
            [tuple(region["xyxy"]) for region in regions],
        )
        audit.append(
            {
                "source": relative_path,
                "size": [width, height],
                "region_count": len(regions),
                "regions": regions,
            }
        )
        print(f"AUDIT fixture={relative_path} region_count={len(regions)}")
    return audit


async def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    engine = MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )
    page9 = await _analyze_page9_stages(engine)
    corpus = await _audit_full_corpus(engine)
    payload = {
        "config": {
            "detection_size": engine._detection_size,
            "text_threshold": engine._text_threshold,
            "box_threshold": engine._box_threshold,
            "unclip_ratio": engine._unclip_ratio,
            "minimum_confidence": engine._ocr_config.prob,
        },
        "page9": page9,
        "corpus": corpus,
    }
    (OUTPUT_ROOT / "audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        "PAGE9_OVERLAY3 "
        f"detector_count={page9['detector_count']} "
        f"target_detector_count={len(page9['target_detector_indices'])} "
        f"recognized_count={page9['recognized_count']} merged_count={page9['merged_count']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
