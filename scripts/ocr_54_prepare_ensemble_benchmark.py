"""Prepare licensed OCR challenge crops for recognizer-comparison experiments.

This script is investigation-only. It intentionally writes recognized fixture text only to
GitHub Actions artifacts, never to ordinary application logs.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
)
from mangasensei.ocr.adapter.recognizer_48px import (
    _copy_quadrilateral,
    _expand_short_axis,
)
from mangasensei.ocr.adapter.recognizer_contract import RECOGNITION_SHORT_AXIS_CONTEXT

FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "tests"
    / "fixtures"
    / "ocr"
    / "real_manga"
    / "black_jack"
)


@dataclass(frozen=True, slots=True)
class ChallengeCase:
    name: str
    relative_path: str
    zone: tuple[int, int, int, int]
    expected: str
    scale: float = 1.0
    match: str = "contains"


CASES = (
    ChallengeCase(
        name="page9-three-column-090",
        relative_path="v01/black_jack_v01_pdf009.jpg",
        zone=(130, 285, 455, 745),
        expected="国家試験に合格しなければいけない",
        scale=0.9,
    ),
    ChallengeCase(
        name="page73-lexical-kiriguchi",
        relative_path="v01/black_jack_v01_pdf073.jpg",
        zone=(1000, 1210, 80, 360),
        expected="見事な切り口です教授",
    ),
    ChallengeCase(
        name="page73-short-umu",
        relative_path="v01/black_jack_v01_pdf073.jpg",
        zone=(500, 620, 250, 420),
        expected="うむ",
    ),
    ChallengeCase(
        name="page90-short-hai",
        relative_path="v01/black_jack_v01_pdf090.jpg",
        zone=(1030, 1210, 160, 480),
        expected="はい",
    ),
    ChallengeCase(
        name="page145-lexical-hakase",
        relative_path="v01/black_jack_v01_pdf145.jpg",
        zone=(760, 1220, 1560, 1810),
        expected="春日部一郎博士",
    ),
    ChallengeCase(
        name="page171-long-footnote",
        relative_path="v01/black_jack_v01_pdf171.jpg",
        zone=(80, 220, 650, 1800),
        expected="※ステント＝心臓の血管",
    ),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, default=Path("var/models"))
    return parser.parse_args()


def _scaled_zone(case: ChallengeCase) -> tuple[int, int, int, int]:
    return tuple(round(value * case.scale) for value in case.zone)  # type: ignore[return-value]


def _center_in_zone(line: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = (float(value) for value in line.xyxy)
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y


def _geometry_key(line: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(line.pts))


def _recognized_by_geometry(lines: list[Any]) -> dict[tuple[tuple[int, int], ...], dict[str, Any]]:
    return {
        _geometry_key(line): {
            "text": str(line.text),
            "confidence": float(line.prob),
        }
        for line in lines
        if str(line.text).strip()
    }


def _save_rgb(path: Path, pixels: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels.astype(np.uint8), mode="RGB").save(path, format="PNG", optimize=True)


def _block_crop(
    pixels: np.ndarray,
    target_lines: list[Any],
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    image_height, image_width = pixels.shape[:2]
    min_x = min(int(line.xyxy[0]) for line in target_lines)
    min_y = min(int(line.xyxy[1]) for line in target_lines)
    max_x = max(int(line.xyxy[2]) for line in target_lines)
    max_y = max(int(line.xyxy[3]) for line in target_lines)
    font_context = max(2, round(max(float(line.font_size) for line in target_lines) * 1.5))
    x1 = max(0, min_x - font_context)
    y1 = max(0, min_y - font_context)
    x2 = min(image_width, max_x + font_context + 1)
    y2 = min(image_height, max_y + font_context + 1)
    if x2 <= x1 or y2 <= y1:
        raise RuntimeError("invalid block crop for benchmark target")
    return pixels[y1:y2, x1:x2], (x1, y1, x2, y2)


async def _run() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, _, manifest = await engine._ensure_loaded()
    original_context = float(recognizer._short_axis_context)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "source_head": "59f12956582a5a3af482339a806585b8650cbd8e",
        "upstream_commit": manifest.upstream_commit,
        "recognition_context_factor": RECOGNITION_SHORT_AXIS_CONTEXT,
        "cases": [],
        "inputs": [],
    }

    for case in CASES:
        source_path = FIXTURE_ROOT / case.relative_path
        source_bytes = source_path.read_bytes()
        with Image.open(io.BytesIO(source_bytes)) as source:
            image = source.convert("RGB")
            if case.scale != 1.0:
                image = image.resize(
                    (round(image.width * case.scale), round(image.height * case.scale)),
                    Image.Resampling.LANCZOS,
                )
        pixels = np.asarray(image)
        zone = _scaled_zone(case)

        started = time.perf_counter()
        textlines, _, _ = await detector.detect(
            pixels,
            engine._detection_size,
            engine._text_threshold,
            engine._box_threshold,
            engine._unclip_ratio,
            *_DETECTOR_FLAGS,
        )
        target_lines = [line for line in textlines if _center_in_zone(line, zone)]
        detector_seconds = time.perf_counter() - started
        if not target_lines:
            raise AssertionError(f"benchmark target has no detector candidates: {case.name}")

        try:
            recognizer._short_axis_context = 1.0
            tight_started = time.perf_counter()
            tight_lines = await recognizer.recognize(
                pixels,
                copy.deepcopy(target_lines),
                engine._ocr_config,
                _RECOGNIZER_FLAG,
            )
            tight_seconds = time.perf_counter() - tight_started

            recognizer._short_axis_context = RECOGNITION_SHORT_AXIS_CONTEXT
            context_started = time.perf_counter()
            context_lines = await recognizer.recognize(
                pixels,
                copy.deepcopy(target_lines),
                engine._ocr_config,
                _RECOGNIZER_FLAG,
            )
            context_seconds = time.perf_counter() - context_started
        finally:
            recognizer._short_axis_context = original_context

        tight_results = _recognized_by_geometry(tight_lines)
        context_results = _recognized_by_geometry(context_lines)
        case_entry: dict[str, Any] = {
            "name": case.name,
            "relative_path": case.relative_path,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "scale": case.scale,
            "zone": zone,
            "expected": case.expected,
            "match": case.match,
            "image_size": [image.width, image.height],
            "detector_candidate_count": len(target_lines),
            "detector_seconds": detector_seconds,
            "tight_48px_seconds": tight_seconds,
            "context_48px_seconds": context_seconds,
            "candidates": [],
        }

        for index, line in enumerate(target_lines):
            direction = str(line.direction)
            tight_quad = _copy_quadrilateral(line)
            context_quad = _expand_short_axis(
                line,
                factor=RECOGNITION_SHORT_AXIS_CONTEXT,
                image_width=image.width,
                image_height=image.height,
            )
            tight_crop = tight_quad.get_transformed_region(pixels, direction, 48)
            context_crop = context_quad.get_transformed_region(pixels, direction, 48)
            key = _geometry_key(line)

            candidate_id = f"{case.name}--line-{index:02d}"
            tight_rel = Path("crops") / f"{candidate_id}--tight.png"
            context_rel = Path("crops") / f"{candidate_id}--context.png"
            _save_rgb(output / tight_rel, tight_crop)
            _save_rgb(output / context_rel, context_crop)

            candidate_entry = {
                "index": index,
                "geometry": [list(point) for point in key],
                "xyxy": [int(value) for value in line.xyxy],
                "direction": direction,
                "detector_probability": float(line.prob),
                "font_size": float(line.font_size),
                "tight_48px": tight_results.get(key),
                "context_48px": context_results.get(key),
                "tight_crop": tight_rel.as_posix(),
                "context_crop": context_rel.as_posix(),
            }
            case_entry["candidates"].append(candidate_entry)
            payload["inputs"].extend(
                [
                    {
                        "id": f"{candidate_id}--tight",
                        "case": case.name,
                        "kind": "line-tight",
                        "path": tight_rel.as_posix(),
                    },
                    {
                        "id": f"{candidate_id}--context",
                        "case": case.name,
                        "kind": "line-context",
                        "path": context_rel.as_posix(),
                    },
                ]
            )

        block, block_bbox = _block_crop(pixels, target_lines)
        block_rel = Path("crops") / f"{case.name}--block.png"
        _save_rgb(output / block_rel, block)
        case_entry["block_crop"] = block_rel.as_posix()
        case_entry["block_bbox"] = list(block_bbox)
        payload["inputs"].append(
            {
                "id": f"{case.name}--block",
                "case": case.name,
                "kind": "block",
                "path": block_rel.as_posix(),
            }
        )
        payload["cases"].append(case_entry)
        print(
            "OCR_ENSEMBLE_PREP "
            f"case={case.name} detector_candidates={len(target_lines)} "
            f"detector_seconds={detector_seconds:.3f}"
        )

    (output / "benchmark.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(_run())
