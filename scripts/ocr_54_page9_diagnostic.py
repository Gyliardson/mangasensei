from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _OcrConfig,
    _RECOGNIZER_FLAG,
    _decode_rgb,
    _manga_reading_order,
    MangaImageTranslatorEngine,
    region_from_upstream,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.detection.default import (
    det_batch_forward_default,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.detection.default_utils import (
    craft_utils,
    dbnet_utils,
    imgproc,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
    det_rearrange_forward,
)

DETECTION_SIZE = 2048
TEXT_THRESHOLD = 0.5
BOX_THRESHOLD = 0.7
UNCLIP_RATIO = 2.3
MINIMUM_CONFIDENCE = 0.2
EXPECTED_PAGE9_SHA256 = "072e3d9c2b54628a6de0c18a0ebe078817f30de545259ab3bcd0eb16974210dd"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _points(value: Any) -> list[list[int]]:
    array = np.asarray(value)
    return [[int(point[0]), int(point[1])] for point in array]


def _xyxy_from_points(value: Any) -> list[int]:
    array = np.asarray(value)
    return [
        int(array[:, 0].min()),
        int(array[:, 1].min()),
        int(array[:, 0].max()),
        int(array[:, 1].max()),
    ]


def _area(value: Any) -> float:
    return float(cv2.contourArea(np.asarray(value, dtype=np.float32)))


def _geometry_key(value: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(value))


def _rearrange_expected(height: int, width: int) -> bool:
    long_side = max(height, width)
    short_side = min(height, width)
    return long_side / DETECTION_SIZE > 2.5 and long_side / short_side > 3


def _build_variants(page9: np.ndarray, page10: np.ndarray | None) -> dict[str, np.ndarray]:
    height, width = page9.shape[:2]
    variants = {"isolated": page9}
    for canvas_height in (3000, 4000, 5200):
        canvas = np.full((canvas_height, width, 3), 255, dtype=np.uint8)
        canvas[:height, :width] = page9
        variants[f"blank_{canvas_height}"] = canvas

    if page10 is not None:
        if page10.shape[1] != width:
            raise ValueError("page 10 width does not match page 9")
        partial_height = min(1000, page10.shape[0])
        combined_partial = np.concatenate((page9, page10[:partial_height]), axis=0)
        variants["real_page10_partial"] = combined_partial
        variants["real_page10_full"] = np.concatenate((page9, page10), axis=0)
    return variants


def _trace_representer(pixels: np.ndarray) -> dict[str, Any]:
    image_height, image_width = pixels.shape[:2]
    db, mask = det_rearrange_forward(
        pixels,
        det_batch_forward_default,
        DETECTION_SIZE,
        4,
        device="cpu",
        verbose=False,
    )

    rearranged = db is not None
    if not rearranged:
        filtered = cv2.bilateralFilter(pixels, 17, 80, 80)
        resized, target_ratio, _, pad_w, pad_h = imgproc.resize_aspect_ratio(
            filtered,
            DETECTION_SIZE,
            cv2.INTER_LINEAR,
            mag_ratio=1,
        )
        resized_height, resized_width = resized.shape[:2]
        ratio_h = ratio_w = 1 / target_ratio
        db, mask = det_batch_forward_default([resized], "cpu")
    else:
        resized_height, resized_width = image_height, image_width
        ratio_h = ratio_w = 1
        pad_h = pad_w = 0
        target_ratio = 1.0

    representer = dbnet_utils.SegDetectorRepresenter(
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        unclip_ratio=UNCLIP_RATIO,
    )
    boxes, scores = representer({"shape": [(resized_height, resized_width)]}, db)
    boxes = boxes[0]
    scores = scores[0]

    raw_candidates: list[dict[str, Any]] = []
    valid_indices: list[int] = []
    for index, (box, score) in enumerate(zip(boxes, scores)):
        nonzero = bool(np.asarray(box).reshape(-1).sum() > 0)
        raw_candidates.append(
            {
                "index": index,
                "score": float(score),
                "nonzero": nonzero,
                "network_points": _points(box),
            }
        )
        if nonzero:
            valid_indices.append(index)

    mapped_candidates: list[dict[str, Any]] = []
    if valid_indices:
        mapped = boxes[valid_indices].astype(np.float64)
        mapped = craft_utils.adjustResultCoordinates(mapped, ratio_w, ratio_h, ratio_net=1)
        mapped = mapped.astype(np.int64)
        for output_index, (source_index, polygon) in enumerate(zip(valid_indices, mapped)):
            polygon_area = _area(polygon)
            mapped_candidates.append(
                {
                    "output_index": output_index,
                    "representer_index": source_index,
                    "score": float(scores[source_index]),
                    "points": _points(polygon),
                    "xyxy": _xyxy_from_points(polygon),
                    "area": polygon_area,
                    "survives_area_filter": polygon_area > 16,
                }
            )

    return {
        "input_dimensions": {"width": image_width, "height": image_height},
        "rearrange_expected": _rearrange_expected(image_height, image_width),
        "rearranged": rearranged,
        "effective_detector_dimensions": {"width": resized_width, "height": resized_height},
        "target_ratio": float(target_ratio),
        "coordinate_scale": {"x": float(ratio_w), "y": float(ratio_h)},
        "padding": {"width": int(pad_w), "height": int(pad_h)},
        "db_shape": [int(value) for value in np.asarray(db).shape],
        "mask_shape": [int(value) for value in np.asarray(mask).shape],
        "representer_slot_count": len(raw_candidates),
        "representer_nonzero_count": len(valid_indices),
        "area_survivor_count": sum(item["survives_area_filter"] for item in mapped_candidates),
        "raw_candidates": raw_candidates,
        "mapped_candidates": mapped_candidates,
    }


def _serialize_textlines(textlines: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "points": _points(line.pts),
            "xyxy": [int(value) for value in line.xyxy],
            "area": float(line.area),
            "score_or_probability": float(line.prob),
            "direction": str(line.direction),
        }
        for index, line in enumerate(textlines)
    ]


def _serialize_recognized(
    recognized: list[Any],
    source_index_by_geometry: dict[tuple[tuple[int, int], ...], int],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for line in recognized:
        output.append(
            {
                "source_candidate_index": source_index_by_geometry.get(_geometry_key(line.pts)),
                "points": _points(line.pts),
                "xyxy": [int(value) for value in line.xyxy],
                "probability": float(line.prob),
                "accepted_nonempty": bool(str(line.text).strip()),
                "assigned_direction": str(getattr(line, "assigned_direction", "")),
            }
        )
    return output


def _serialize_merged(merged: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, block in enumerate(merged):
        output.append(
            {
                "index": index,
                "xyxy": [int(value) for value in block.xyxy],
                "min_rect": _points(block.min_rect[0]),
                "line_count": int(len(block.lines)),
                "line_polygons": [_points(line) for line in block.lines],
                "probability": float(block.prob),
                "has_nonempty_text": bool(str(block.text).strip()),
            }
        )
    return output


def _serialize_final(regions: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {
            "reading_order": int(region.reading_order),
            "bbox": {
                "x": int(region.bbox.x),
                "y": int(region.bbox.y),
                "width": int(region.bbox.width),
                "height": int(region.bbox.height),
            },
            "polygon": [list(point) for point in region.polygon] if region.polygon else None,
            "confidence": float(region.confidence),
            "has_nonempty_text": bool(region.japanese_text),
        }
        for region in regions
    ]


def _crop_dimensions(recognizer: Any, pixels: np.ndarray, textlines: list[Any]) -> list[dict[str, Any]]:
    source = copy.deepcopy(textlines)
    directions = list(recognizer._generate_text_direction(source))
    source_index_by_geometry = {
        _geometry_key(line.pts): index for index, line in enumerate(source)
    }
    output: list[dict[str, Any]] = []
    for line, direction in directions:
        crop = line.get_transformed_region(pixels, direction, 48)
        output.append(
            {
                "source_candidate_index": source_index_by_geometry.get(_geometry_key(line.pts)),
                "direction": str(direction),
                "crop_width": int(crop.shape[1]),
                "crop_height": int(crop.shape[0]),
            }
        )
    return output


def _draw_overlay(image: np.ndarray, items: list[tuple[list[list[int]], int]], path: Path) -> None:
    pil = Image.fromarray(image)
    draw = ImageDraw.Draw(pil)
    for points, index in items:
        polygon = [(point[0], point[1]) for point in points]
        if polygon:
            draw.line(polygon + [polygon[0]], fill=(255, 0, 0), width=3)
            anchor = polygon[0]
            draw.rectangle(
                (anchor[0], anchor[1], anchor[0] + 30, anchor[1] + 20),
                fill=(255, 255, 255),
            )
            draw.text((anchor[0] + 2, anchor[1] + 2), str(index), fill=(0, 0, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    pil.save(path, format="PNG", compress_level=9)


async def _analyze_variant(
    detector: Any,
    recognizer: Any,
    merge: Any,
    manifest: Any,
    name: str,
    pixels: np.ndarray,
    output_dir: Path,
) -> dict[str, Any]:
    height, width = pixels.shape[:2]
    representer = _trace_representer(pixels)

    textlines, _, _ = await detector.detect(
        pixels,
        DETECTION_SIZE,
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        UNCLIP_RATIO,
        *_DETECTOR_FLAGS,
    )
    detector_candidates = _serialize_textlines(textlines)
    source_index_by_geometry = {
        _geometry_key(line.pts): index for index, line in enumerate(textlines)
    }
    crops = _crop_dimensions(recognizer, pixels, textlines)

    production_input = copy.deepcopy(textlines)
    recognized = await recognizer.recognize(
        pixels,
        production_input,
        _OcrConfig(prob=MINIMUM_CONFIDENCE),
        _RECOGNIZER_FLAG,
    )
    recognized_nonempty = [line for line in recognized if str(line.text).strip()]

    diagnostic_input = copy.deepcopy(textlines)
    recognized_zero = await recognizer.recognize(
        pixels,
        diagnostic_input,
        _OcrConfig(prob=0.0),
        _RECOGNIZER_FLAG,
    )
    recognized_zero_nonempty = [line for line in recognized_zero if str(line.text).strip()]

    merged = await merge(recognized_nonempty, width, height)
    ordered = _manga_reading_order(merged, page_height=height)

    raw_bytes = Image.fromarray(pixels).tobytes()
    synthetic_sha = hashlib.sha256(raw_bytes).hexdigest()
    dimensions = PageDimensions(width=width, height=height)
    final_regions = tuple(
        region_from_upstream(
            block,
            image_sha256=synthetic_sha,
            dimensions=dimensions,
            reading_order=index,
            upstream_commit=manifest.upstream_commit,
        )
        for index, block in enumerate(ordered[:128])
        if str(block.text).strip()
    )

    serialized_recognized = _serialize_recognized(
        recognized_nonempty,
        source_index_by_geometry,
    )
    serialized_merged = _serialize_merged(merged)
    variant_dir = output_dir / name
    _draw_overlay(
        pixels,
        [(item["points"], item["index"]) for item in detector_candidates],
        variant_dir / "detector.png",
    )
    _draw_overlay(
        pixels,
        [
            (
                item["points"],
                int(item["source_candidate_index"])
                if item["source_candidate_index"] is not None
                else -1,
            )
            for item in serialized_recognized
        ],
        variant_dir / "recognized.png",
    )
    _draw_overlay(
        pixels,
        [(item["min_rect"], item["index"]) for item in serialized_merged],
        variant_dir / "merged.png",
    )

    return {
        "name": name,
        "dimensions": {"width": width, "height": height},
        "representer": representer,
        "detector_candidates": detector_candidates,
        "recognizer_crops": crops,
        "recognized_production": serialized_recognized,
        "recognized_zero_threshold": _serialize_recognized(
            recognized_zero_nonempty,
            source_index_by_geometry,
        ),
        "merged_blocks": serialized_merged,
        "final_regions": _serialize_final(final_regions),
        "counts": {
            "detector_candidates": len(textlines),
            "recognized_production": len(recognized_nonempty),
            "recognized_zero_threshold": len(recognized_zero_nonempty),
            "merged_blocks": len(merged),
            "final_regions": len(final_regions),
        },
    }


async def _main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.WARNING)
    page9_content = args.page9.read_bytes()
    if _sha256(page9_content) != EXPECTED_PAGE9_SHA256:
        raise ValueError("page 9 bytes do not match the reviewed embedded JPEG SHA-256")

    page9 = _decode_rgb(page9_content)
    if page9.shape[:2] != (2000, 1414):
        raise ValueError(f"unexpected page 9 dimensions: {page9.shape[:2]}")

    page10: np.ndarray | None = None
    page10_metadata: dict[str, Any] | None = None
    if args.page10 is not None:
        page10_content = args.page10.read_bytes()
        page10 = _decode_rgb(page10_content)
        page10_metadata = {
            "sha256": _sha256(page10_content),
            "width": int(page10.shape[1]),
            "height": int(page10.shape[0]),
        }

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, merge, manifest = await engine._ensure_loaded()

    args.output.mkdir(parents=True, exist_ok=True)
    variants = _build_variants(page9, page10)
    observations = []
    for name, pixels in variants.items():
        observations.append(
            await _analyze_variant(
                detector,
                recognizer,
                merge,
                manifest,
                name,
                pixels,
                args.output,
            )
        )

    report = {
        "schema_version": 1,
        "privacy": (
            "No recognized manga text is serialized; diagnostics contain geometry, "
            "scores, counts, dimensions and local-only overlays."
        ),
        "production_config": {
            "detection_size": DETECTION_SIZE,
            "text_threshold": TEXT_THRESHOLD,
            "box_threshold": BOX_THRESHOLD,
            "unclip_ratio": UNCLIP_RATIO,
            "minimum_confidence": MINIMUM_CONFIDENCE,
            "device": "cpu",
        },
        "page9": {
            "sha256": _sha256(page9_content),
            "width": 1414,
            "height": 2000,
        },
        "page10": page10_metadata,
        "model_manifest_version": manifest.version,
        "upstream_commit": manifest.upstream_commit,
        "observations": observations,
    }
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    summary = {
        observation["name"]: {
            "dimensions": observation["dimensions"],
            "rearranged": observation["representer"]["rearranged"],
            "effective_detector_dimensions": observation["representer"][
                "effective_detector_dimensions"
            ],
            "counts": observation["counts"],
        }
        for observation in observations
    }
    print(json.dumps(summary, sort_keys=True))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page9", type=Path, required=True)
    parser.add_argument("--page10", type=Path)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
