from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import os
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
    _manga_reading_order,
    region_from_upstream,
)
from mangasensei.ocr.contracts import OcrRegionResult
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)

FIXTURE_ROOT = Path("tests/fixtures/ocr/real_manga/black_jack")
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
PAGE9_PATH = FIXTURE_ROOT / "v01/black_jack_v01_pdf009.jpg"
OUTPUT_ROOT = Path(os.environ.get("MANGASENSEI_OCR_AUDIT_DIR", "var/ocr54-context-corpus"))
FACTORS = (1.0, 1.12, 1.16)
TARGET_ZONE = (130, 285, 455, 745)
TARGET_ANCHOR = "国家試験に合格しなければいけない"


def _geometry_key(points: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(points))


def _xyxy(value: Any) -> tuple[int, int, int, int]:
    return tuple(int(round(float(item))) for item in value.xyxy)  # type: ignore[return-value]


def _center_in_zone(value: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = _xyxy(value)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def _expand_short_axis(
    line: Quadrilateral,
    factor: float,
    width: int,
    height: int,
) -> Quadrilateral:
    if factor == 1.0:
        return copy.deepcopy(line)

    points = np.asarray(line.pts, dtype=np.float64)
    center = points.mean(axis=0)
    structure = [np.asarray(point, dtype=np.float64) for point in line.structure]
    if line.direction == "v":
        long_vector = structure[1] - structure[0]
    else:
        long_vector = structure[3] - structure[2]
    long_norm = float(np.linalg.norm(long_vector))
    if long_norm == 0:
        return copy.deepcopy(line)

    long_unit = long_vector / long_norm
    short_unit = np.asarray((-long_unit[1], long_unit[0]), dtype=np.float64)
    expanded = []
    for point in points:
        delta = point - center
        long_component = float(np.dot(delta, long_unit))
        short_component = float(np.dot(delta, short_unit)) * factor
        expanded.append(center + long_component * long_unit + short_component * short_unit)

    result = Quadrilateral(
        np.rint(np.asarray(expanded)).astype(np.int64),
        "",
        float(line.prob),
        int(line.fg_r),
        int(line.fg_g),
        int(line.fg_b),
        int(line.bg_r),
        int(line.bg_g),
        int(line.bg_b),
    )
    result.clip(width, height)
    return result


def _restore_recognized_geometry(
    recognized: list[Quadrilateral],
    expanded_lines: list[Quadrilateral],
    original_lines: list[Quadrilateral],
) -> list[Quadrilateral]:
    expanded_index = {
        _geometry_key(line.pts): index for index, line in enumerate(expanded_lines)
    }
    restored: list[Quadrilateral] = []
    for recognized_line in recognized:
        source_index = expanded_index.get(_geometry_key(recognized_line.pts))
        if source_index is None:
            raise RuntimeError("recognized geometry did not match an expanded detector line")
        original = copy.deepcopy(original_lines[source_index])
        original.text = str(recognized_line.text)
        original.prob = float(recognized_line.prob)
        original.fg_r = int(recognized_line.fg_r)
        original.fg_g = int(recognized_line.fg_g)
        original.fg_b = int(recognized_line.fg_b)
        original.bg_r = int(recognized_line.bg_r)
        original.bg_g = int(recognized_line.bg_g)
        original.bg_b = int(recognized_line.bg_b)
        original.assigned_direction = getattr(recognized_line, "assigned_direction", None)
        restored.append(original)
    return restored


def _serialize_line(line: Quadrilateral) -> dict[str, Any]:
    return {
        "xyxy": list(_xyxy(line)),
        "probability": float(line.prob),
        "text": str(line.text),
        "direction": str(line.direction),
    }


def _serialize_region(region: OcrRegionResult) -> dict[str, Any]:
    return {
        "xyxy": [
            region.bbox.x,
            region.bbox.y,
            region.bbox.x + region.bbox.width,
            region.bbox.y + region.bbox.height,
        ],
        "confidence": float(region.confidence),
        "text": region.japanese_text,
        "reading_order": int(region.reading_order),
    }


def _draw_regions(
    source: Image.Image,
    destination: Path,
    regions: list[OcrRegionResult],
) -> None:
    image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    line_width = max(2, round(max(image.size) / 700))
    for index, region in enumerate(regions):
        x1 = region.bbox.x
        y1 = region.bbox.y
        x2 = x1 + region.bbox.width
        y2 = y1 + region.bbox.height
        draw.rectangle((x1, y1, x2, y2), outline=(220, 20, 60), width=line_width)
        tx = max(0, min(x1, image.width - 20))
        ty = max(0, y1 - 14)
        draw.rectangle((tx, ty, tx + 20, ty + 14), fill=(255, 255, 255))
        draw.text((tx + 2, ty + 1), str(index), fill=(0, 0, 0), font=font)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, quality=92)


async def _run_factor(
    engine: MangaImageTranslatorEngine,
    detector_lines: list[Quadrilateral],
    pixels: np.ndarray,
    factor: float,
    image_sha256: str,
) -> dict[str, Any]:
    _, recognizer, merge, manifest = await engine._ensure_loaded()
    height, width = pixels.shape[:2]
    originals = copy.deepcopy(detector_lines)
    expanded = [_expand_short_axis(line, factor, width, height) for line in originals]
    recognized_expanded = await recognizer.recognize(
        pixels,
        expanded,
        engine._ocr_config,
        _RECOGNIZER_FLAG,
    )
    recognized_expanded = [line for line in recognized_expanded if str(line.text).strip()]
    recognized = _restore_recognized_geometry(recognized_expanded, expanded, originals)
    merged = await merge(copy.deepcopy(recognized), width, height)
    ordered = _manga_reading_order(merged, page_height=height)
    dimensions = PageDimensions(width=width, height=height)
    regions = [
        region_from_upstream(
            block,
            image_sha256=image_sha256,
            dimensions=dimensions,
            reading_order=index,
            upstream_commit=manifest.upstream_commit,
        )
        for index, block in enumerate(ordered[:128])
        if str(block.text).strip()
    ]
    return {
        "factor": factor,
        "recognized_count": len(recognized),
        "recognized": [_serialize_line(line) for line in recognized],
        "merged_count": len(merged),
        "regions": regions,
        "region_count": len(regions),
    }


async def _audit_fixture(
    engine: MangaImageTranslatorEngine,
    relative_path: str,
) -> dict[str, Any]:
    detector, _, _, _ = await engine._ensure_loaded()
    path = FIXTURE_ROOT / relative_path
    content = path.read_bytes()
    pixels = _decode_rgb(content)
    image_sha256 = hashlib.sha256(content).hexdigest()
    with Image.open(path) as opened:
        source = opened.convert("RGB")
    detector_lines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    runs: list[dict[str, Any]] = []
    for factor in FACTORS:
        run = await _run_factor(
            engine,
            copy.deepcopy(detector_lines),
            pixels,
            factor,
            image_sha256,
        )
        regions = run.pop("regions")
        _draw_regions(
            source,
            OUTPUT_ROOT / "corpus" / f"factor-{factor:.2f}" / f"{Path(relative_path).stem}.jpg",
            regions,
        )
        run["final_regions"] = [_serialize_region(region) for region in regions]
        runs.append(run)
        print(
            "CORPUS_CONTEXT "
            f"fixture={relative_path} factor={factor:.2f} "
            f"recognized={run['recognized_count']} regions={run['region_count']}"
        )
    return {
        "source": relative_path,
        "size": [source.width, source.height],
        "detector_count": len(detector_lines),
        "runs": runs,
    }


def _scale_page9_90() -> tuple[bytes, Image.Image]:
    with Image.open(PAGE9_PATH) as opened:
        source = opened.convert("RGB")
        resized = source.resize(
            (round(source.width * 0.9), round(source.height * 0.9)),
            Image.Resampling.LANCZOS,
        )
    out = io.BytesIO()
    resized.save(out, format="PNG", optimize=True)
    return out.getvalue(), resized


async def _audit_scaled_page9(engine: MangaImageTranslatorEngine) -> dict[str, Any]:
    detector, _, _, _ = await engine._ensure_loaded()
    content, source = _scale_page9_90()
    pixels = _decode_rgb(content)
    image_sha256 = hashlib.sha256(content).hexdigest()
    detector_lines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    scaled_zone = tuple(round(value * 0.9) for value in TARGET_ZONE)
    target_detector = [line for line in detector_lines if _center_in_zone(line, scaled_zone)]
    runs: list[dict[str, Any]] = []
    for factor in FACTORS:
        run = await _run_factor(
            engine,
            copy.deepcopy(detector_lines),
            pixels,
            factor,
            image_sha256,
        )
        regions = run.pop("regions")
        _draw_regions(
            source,
            OUTPUT_ROOT / "scaled-page9" / f"factor-{factor:.2f}.jpg",
            regions,
        )
        target_regions = [
            region for region in regions
            if _center_in_zone(
                type(
                    "RegionBox",
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
                scaled_zone,
            )
        ]
        run["target_regions"] = [_serialize_region(region) for region in target_regions]
        run["target_anchor_present"] = any(
            TARGET_ANCHOR in region.japanese_text for region in target_regions
        )
        run["final_regions"] = [_serialize_region(region) for region in regions]
        runs.append(run)
        print(
            "SCALED_PAGE9_CONTEXT "
            f"factor={factor:.2f} target_anchor={run['target_anchor_present']} "
            f"regions={run['region_count']}"
        )
    return {
        "size": [source.width, source.height],
        "detector_count": len(detector_lines),
        "target_detector_count": len(target_detector),
        "target_detector": [_serialize_line(line) for line in target_detector],
        "runs": runs,
    }


async def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    engine = MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    corpus = []
    for entry in manifest["fixtures"]:
        corpus.append(await _audit_fixture(engine, str(entry["file"])))
    scaled_page9 = await _audit_scaled_page9(engine)
    payload = {
        "factors": list(FACTORS),
        "policy": (
            "Context is applied only to recognizer input crops. Recognized text/confidence/colors "
            "are copied back to the original detector quadrilateral before merge, so detector and "
            "final region geometry are not widened merely by this experiment."
        ),
        "corpus": corpus,
        "scaled_page9": scaled_page9,
    }
    (OUTPUT_ROOT / "context-corpus.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
