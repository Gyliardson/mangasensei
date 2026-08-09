from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
    _OcrConfig,
)

DETECTION_SIZE = 2048
TEXT_THRESHOLD = 0.5
BOX_THRESHOLD = 0.7
UNCLIP_RATIO = 2.3
EXPECTED_PAGE9_SHA256 = "072e3d9c2b54628a6de0c18a0ebe078817f30de545259ab3bcd0eb16974210dd"
TARGET_ROI = (160, 930, 290, 1220)
REPEAT_RUNS = 3


@dataclass(frozen=True, slots=True)
class _CropRecord:
    source_index: int
    crop_width: int
    crop_height: int
    direction: str


def _intersects(box: tuple[int, int, int, int] | list[int]) -> bool:
    x0, y0, x1, y1 = box
    rx0, ry0, rx1, ry1 = TARGET_ROI
    return x1 >= rx0 and x0 <= rx1 and y1 >= ry0 and y0 <= ry1


def _geometry_key(points: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(points))


def _expected_tensor_width(max_crop_width: int) -> int:
    # Mirrors the current vendored Model48pxOCR batching expression exactly.
    return 4 * (max_crop_width + 7) // 4


def _backbone_feature_width(input_width: int) -> int:
    # The 48px recognizer reduces width by two stride-2 convolutions.
    return input_width // 4


def _valid_feature_length(crop_width: int) -> int:
    # Mirrors OCR.infer_beam_batch_tensor().
    return (crop_width + 3) // 4 + 2


def _crop_records(recognizer: Any, pixels: np.ndarray, textlines: list[Any]) -> list[_CropRecord]:
    source = copy.deepcopy(textlines)
    source_index_by_geometry = {
        _geometry_key(line.pts): index for index, line in enumerate(source)
    }
    records: list[_CropRecord] = []
    for line, direction in recognizer._generate_text_direction(source):
        crop = line.get_transformed_region(pixels, direction, 48)
        source_index = source_index_by_geometry.get(_geometry_key(line.pts))
        if source_index is None:
            raise RuntimeError("could not map recognizer crop back to detector candidate")
        records.append(
            _CropRecord(
                source_index=source_index,
                crop_width=int(crop.shape[1]),
                crop_height=int(crop.shape[0]),
                direction=str(direction),
            )
        )
    return records


async def _recognize_scenario(
    recognizer: Any,
    pixels: np.ndarray,
    textlines: list[Any],
    crop_by_index: dict[int, _CropRecord],
    indices: list[int],
    target_source_index: int,
) -> dict[str, Any]:
    max_crop_width = max(crop_by_index[index].crop_width for index in indices)
    input_width = _expected_tensor_width(max_crop_width)
    memory_width = _backbone_feature_width(input_width)
    target_width = crop_by_index[target_source_index].crop_width
    valid_length = _valid_feature_length(target_width)

    runs: list[dict[str, Any]] = []
    for _ in range(REPEAT_RUNS):
        source = [copy.deepcopy(textlines[index]) for index in indices]
        source_geometry = {
            _geometry_key(line.pts): index for index, line in zip(indices, source, strict=True)
        }
        recognized = await recognizer.recognize(
            pixels,
            source,
            _OcrConfig(prob=0.0),
            _RECOGNIZER_FLAG,
        )
        target = next(
            (
                line
                for line in recognized
                if source_geometry.get(_geometry_key(line.pts)) == target_source_index
            ),
            None,
        )
        runs.append(
            {
                "present_at_zero_threshold": target is not None,
                "probability": float(target.prob) if target is not None else None,
                "passes_production_confidence": bool(
                    target is not None and float(target.prob) >= 0.2 and str(target.text).strip()
                ),
                "codepoints": len(str(target.text)) if target is not None else 0,
            }
        )

    return {
        "source_indices": indices,
        "batch_size": len(indices),
        "max_crop_width": max_crop_width,
        "tensor_input_width": input_width,
        "backbone_memory_width": memory_width,
        "target_crop_width": target_width,
        "target_valid_feature_length": valid_length,
        "target_available_context_features": memory_width - valid_length,
        "runs": runs,
    }


async def _main(args: argparse.Namespace) -> None:
    content = args.page9.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != EXPECTED_PAGE9_SHA256:
        raise ValueError("page 9 bytes do not match the reviewed embedded JPEG SHA-256")
    pixels = _decode_rgb(content)
    if pixels.shape[:2] != (2000, 1414):
        raise ValueError(f"unexpected page 9 dimensions: {pixels.shape[:2]}")

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, _, manifest = await engine._ensure_loaded()
    textlines, _, _ = await detector.detect(
        pixels,
        DETECTION_SIZE,
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        UNCLIP_RATIO,
        *_DETECTOR_FLAGS,
    )
    crops = _crop_records(recognizer, pixels, textlines)
    crop_by_index = {record.source_index: record for record in crops}

    target_indices = [
        index
        for index, line in enumerate(textlines)
        if _intersects([int(value) for value in line.xyxy])
    ]
    if len(target_indices) != 2:
        raise ValueError(f"expected two target detector lines, found {len(target_indices)}")
    target_indices.sort(key=lambda index: int(textlines[index].xyxy[0]))
    left_index, right_index = target_indices
    left_width = crop_by_index[left_index].crop_width

    wider_indices = sorted(
        (
            record.source_index
            for record in crops
            if record.crop_width > left_width
        ),
        key=lambda index: crop_by_index[index].crop_width,
    )
    if not wider_indices:
        raise ValueError("expected at least one detector line wider than the fragile target line")
    nearest_wider = wider_indices[0]
    widest = max(crops, key=lambda record: record.crop_width).source_index

    scenarios = {
        "left_only": [left_index],
        "target_pair": [left_index, right_index],
        "left_plus_nearest_wider": [left_index, nearest_wider],
        "left_plus_widest": [left_index, widest],
        "all_candidates": list(range(len(textlines))),
    }
    observations = {
        name: await _recognize_scenario(
            recognizer,
            pixels,
            textlines,
            crop_by_index,
            indices,
            left_index,
        )
        for name, indices in scenarios.items()
    }

    report = {
        "schema_version": 1,
        "privacy": (
            "No recognized manga text or source image bytes are serialized; only geometry, "
            "crop widths, feature lengths, probabilities and codepoint counts are recorded."
        ),
        "page9": {"sha256": digest, "width": 1414, "height": 2000},
        "model_manifest_version": manifest.version,
        "upstream_commit": manifest.upstream_commit,
        "production_config": {
            "detection_size": DETECTION_SIZE,
            "text_threshold": TEXT_THRESHOLD,
            "box_threshold": BOX_THRESHOLD,
            "unclip_ratio": UNCLIP_RATIO,
            "minimum_confidence": 0.2,
            "device": "cpu",
        },
        "target": {
            "left_source_index": left_index,
            "right_source_index": right_index,
            "left_xyxy": [int(value) for value in textlines[left_index].xyxy],
            "right_xyxy": [int(value) for value in textlines[right_index].xyxy],
            "left_crop_width": left_width,
            "right_crop_width": crop_by_index[right_index].crop_width,
            "nearest_wider_source_index": nearest_wider,
            "nearest_wider_crop_width": crop_by_index[nearest_wider].crop_width,
            "widest_source_index": widest,
            "widest_crop_width": crop_by_index[widest].crop_width,
        },
        "observations": observations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                name: {
                    "margin": item["target_available_context_features"],
                    "probabilities": [run["probability"] for run in item["runs"]],
                    "passes": [run["passes_production_confidence"] for run in item["runs"]],
                }
                for name, item in observations.items()
            },
            sort_keys=True,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page9", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
