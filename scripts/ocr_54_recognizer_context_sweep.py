from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import einops
import numpy as np
import torch

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    MangaImageTranslatorEngine,
    _decode_rgb,
)

DETECTION_SIZE = 2048
TEXT_THRESHOLD = 0.5
BOX_THRESHOLD = 0.7
UNCLIP_RATIO = 2.3
PRODUCTION_CONFIDENCE = 0.2
EXPECTED_PAGE9_SHA256 = "072e3d9c2b54628a6de0c18a0ebe078817f30de545259ab3bcd0eb16974210dd"
TARGET_ROI = (160, 930, 290, 1220)
CONTEXT_FEATURES = (0, 1, 2, 4, 8, 16, 32)
REPEAT_RUNS = 3


def _intersects(box: tuple[int, int, int, int] | list[int]) -> bool:
    x0, y0, x1, y1 = box
    rx0, ry0, rx1, ry1 = TARGET_ROI
    return x1 >= rx0 and x0 <= rx1 and y1 >= ry0 and y0 <= ry1


def _geometry_key(points: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(points))


def _current_tensor_width(crop_width: int) -> int:
    return 4 * (crop_width + 7) // 4


def _valid_feature_length(crop_width: int) -> int:
    return (crop_width + 3) // 4 + 2


def _tensor_width_for_context(crop_width: int, context_features: int) -> int:
    return 4 * (_valid_feature_length(crop_width) + context_features)


def _decoded_codepoints(model: Any, indices: torch.Tensor) -> int:
    count = 0
    for token_id in indices:
        token = model.dictionary[int(token_id)]
        if token == "</S>":
            break
        if token in {"<S>", "<PAD>"}:
            continue
        count += 1
    return count


def _infer_at_width(
    recognizer: Any,
    crop: np.ndarray,
    tensor_width: int,
) -> dict[str, Any]:
    crop_width = int(crop.shape[1])
    if tensor_width < crop_width:
        raise ValueError("tensor width must not truncate the target crop")

    region = np.zeros((1, 48, tensor_width, 3), dtype=np.uint8)
    region[0, :, :crop_width, :] = crop
    tensor = (torch.from_numpy(region).float() - 127.5) / 127.5
    tensor = einops.rearrange(tensor, "N H W C -> N C H W")
    if recognizer.use_gpu:
        tensor = tensor.to(recognizer.device)

    runs: list[dict[str, Any]] = []
    for _ in range(REPEAT_RUNS):
        with torch.no_grad():
            result = recognizer.model.infer_beam_batch_tensor(
                tensor,
                [crop_width],
                beams_k=5,
                max_seq_length=255,
            )[0]
        pred_chars_index, probability, *_ = result
        runs.append(
            {
                "probability": float(probability),
                "passes_production_confidence": bool(
                    float(probability) >= PRODUCTION_CONFIDENCE
                ),
                "codepoints": _decoded_codepoints(
                    recognizer.model,
                    pred_chars_index,
                ),
            }
        )

    memory_width = tensor_width // 4
    valid_length = _valid_feature_length(crop_width)
    return {
        "tensor_input_width": tensor_width,
        "backbone_memory_width": memory_width,
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

    target_indices = [
        index
        for index, line in enumerate(textlines)
        if _intersects([int(value) for value in line.xyxy])
    ]
    if len(target_indices) != 2:
        raise ValueError(f"expected two target detector lines, found {len(target_indices)}")
    target_indices.sort(key=lambda index: int(textlines[index].xyxy[0]))
    left_index = target_indices[0]

    source = copy.deepcopy(textlines)
    source_index_by_geometry = {
        _geometry_key(line.pts): index for index, line in enumerate(source)
    }
    target_crop: np.ndarray | None = None
    for line, direction in recognizer._generate_text_direction(source):
        source_index = source_index_by_geometry.get(_geometry_key(line.pts))
        if source_index != left_index:
            continue
        target_crop = line.get_transformed_region(pixels, direction, 48)
        break
    if target_crop is None:
        raise RuntimeError("could not construct the reviewed target crop")

    crop_width = int(target_crop.shape[1])
    current_width = _current_tensor_width(crop_width)
    observations: dict[str, dict[str, Any]] = {
        "current": _infer_at_width(recognizer, target_crop, current_width)
    }
    for context_features in CONTEXT_FEATURES:
        tensor_width = _tensor_width_for_context(crop_width, context_features)
        observations[f"context_{context_features}"] = _infer_at_width(
            recognizer,
            target_crop,
            tensor_width,
        )

    report = {
        "schema_version": 1,
        "privacy": (
            "No recognized manga text or source image bytes are serialized; only "
            "tensor geometry, probabilities and codepoint counts are recorded."
        ),
        "page9": {"sha256": digest, "width": 1414, "height": 2000},
        "model_manifest_version": manifest.version,
        "upstream_commit": manifest.upstream_commit,
        "production_confidence": PRODUCTION_CONFIDENCE,
        "target": {
            "source_index": left_index,
            "xyxy": [int(value) for value in textlines[left_index].xyxy],
            "crop_width": crop_width,
            "valid_feature_length": _valid_feature_length(crop_width),
        },
        "observations": observations,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                name: {
                    "context_features": item["target_available_context_features"],
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
