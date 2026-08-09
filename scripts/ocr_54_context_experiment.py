from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
    _manga_reading_order,
    _OcrConfig,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)

DETECTION_SIZE = 2048
TEXT_THRESHOLD = 0.5
BOX_THRESHOLD = 0.7
UNCLIP_RATIO = 2.3
MINIMUM_CONFIDENCE = 0.2
EXPECTED_PAGE9_SHA256 = "072e3d9c2b54628a6de0c18a0ebe078817f30de545259ab3bcd0eb16974210dd"
TARGET_ROI = (160, 930, 290, 1220)
CONTEXT_FACTORS = (1.0, 1.2, 1.35, 1.5)
STRESS_FIXTURES = (
    "v01/black_jack_v01_pdf021.jpg",
    "v01/black_jack_v01_pdf041.jpg",
    "v01/black_jack_v01_pdf145.jpg",
    "v01/black_jack_v01_pdf201.jpg",
)
SHORT_TEXT_FIXTURES = (
    ("v01/black_jack_v01_pdf073.jpg", "うむ"),
    ("v01/black_jack_v01_pdf090.jpg", "はい"),
)


def _intersects(box: tuple[int, int, int, int] | list[int]) -> bool:
    x0, y0, x1, y1 = box
    rx0, ry0, rx1, ry1 = TARGET_ROI
    return x1 >= rx0 and x0 <= rx1 and y1 >= ry0 and y0 <= ry1


def _expand_short_axis(line: Quadrilateral, factor: float, width: int, height: int) -> Quadrilateral:
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
    expanded: list[np.ndarray] = []
    for point in points:
        delta = point - center
        long_component = float(np.dot(delta, long_unit))
        short_component = float(np.dot(delta, short_unit)) * factor
        expanded.append(center + long_component * long_unit + short_component * short_unit)

    result = Quadrilateral(
        np.rint(np.asarray(expanded)).astype(np.int64),
        "",
        float(line.prob),
    )
    result.clip(width, height)
    return result


def _geometry_key(points: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(points))


async def _analyze_variant(
    recognizer: Any,
    merge: Any,
    pixels: np.ndarray,
    source_lines: list[Quadrilateral],
    factor: float,
    *,
    target_page: bool,
    expected_short_text: str | None = None,
) -> dict[str, Any]:
    height, width = pixels.shape[:2]
    expanded = [_expand_short_axis(line, factor, width, height) for line in source_lines]
    source_index_by_geometry = {
        _geometry_key(line.pts): index for index, line in enumerate(expanded)
    }
    recognized = await recognizer.recognize(
        pixels,
        expanded,
        _OcrConfig(prob=MINIMUM_CONFIDENCE),
        _RECOGNIZER_FLAG,
    )
    recognized = [line for line in recognized if str(line.text).strip()]
    merged = await merge(recognized, width, height)
    ordered = _manga_reading_order(merged, page_height=height)

    report: dict[str, Any] = {
        "factor": factor,
        "detector_candidates": len(source_lines),
        "recognized_candidates": len(recognized),
        "merged_blocks": len(merged),
        "final_regions": len(ordered[:128]),
        "recognized_codepoints_total": sum(len(str(line.text)) for line in recognized),
        "middle_dot_codepoints_total": sum(str(line.text).count("・") for line in recognized),
    }

    if target_page:
        target_lines = []
        for line in recognized:
            box = [int(value) for value in line.xyxy]
            if not _intersects(box):
                continue
            target_lines.append(
                {
                    "source_candidate_index": source_index_by_geometry.get(
                        _geometry_key(line.pts)
                    ),
                    "xyxy": box,
                    "probability": float(line.prob),
                    "codepoints": len(str(line.text)),
                    "middle_dot_codepoints": str(line.text).count("・"),
                }
            )
        target_blocks = [
            {
                "xyxy": [int(value) for value in block.xyxy],
                "line_count": int(len(block.lines)),
                "codepoints": len(str(block.text)),
                "middle_dot_codepoints": str(block.text).count("・"),
            }
            for block in ordered
            if _intersects([int(value) for value in block.xyxy])
        ]
        report["target_lines"] = target_lines
        report["target_blocks"] = target_blocks

    if expected_short_text is not None:
        report["reviewed_short_text_present"] = any(
            expected_short_text in str(line.text) for line in recognized
        )

    return report


async def _analyze_image(
    detector: Any,
    recognizer: Any,
    merge: Any,
    pixels: np.ndarray,
    *,
    target_page: bool,
    expected_short_text: str | None = None,
) -> dict[str, Any]:
    textlines, _, _ = await detector.detect(
        pixels,
        DETECTION_SIZE,
        TEXT_THRESHOLD,
        BOX_THRESHOLD,
        UNCLIP_RATIO,
        *_DETECTOR_FLAGS,
    )
    variants = []
    for factor in CONTEXT_FACTORS:
        variants.append(
            await _analyze_variant(
                recognizer,
                merge,
                pixels,
                textlines,
                factor,
                target_page=target_page,
                expected_short_text=expected_short_text,
            )
        )
    return {"variants": variants}


async def _main(args: argparse.Namespace) -> None:
    page9_content = args.page9.read_bytes()
    digest = hashlib.sha256(page9_content).hexdigest()
    if digest != EXPECTED_PAGE9_SHA256:
        raise ValueError("page 9 bytes do not match the reviewed embedded JPEG SHA-256")
    page9 = _decode_rgb(page9_content)
    if page9.shape[:2] != (2000, 1414):
        raise ValueError(f"unexpected page 9 dimensions: {page9.shape[:2]}")

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, merge, manifest = await engine._ensure_loaded()

    report: dict[str, Any] = {
        "schema_version": 1,
        "privacy": (
            "No recognized manga text or source image bytes are serialized; only "
            "counts, target geometry and punctuation/codepoint counts are recorded."
        ),
        "production_config": {
            "detection_size": DETECTION_SIZE,
            "text_threshold": TEXT_THRESHOLD,
            "box_threshold": BOX_THRESHOLD,
            "unclip_ratio": UNCLIP_RATIO,
            "minimum_confidence": MINIMUM_CONFIDENCE,
            "device": "cpu",
        },
        "context_factors": list(CONTEXT_FACTORS),
        "page9": {
            "sha256": digest,
            "width": 1414,
            "height": 2000,
            **await _analyze_image(
                detector,
                recognizer,
                merge,
                page9,
                target_page=True,
            ),
        },
        "model_manifest_version": manifest.version,
        "upstream_commit": manifest.upstream_commit,
        "stress": {},
        "short_text": {},
    }

    for relative_path in STRESS_FIXTURES:
        image_path = args.fixture_root / relative_path
        with Image.open(image_path) as image:
            pixels = np.asarray(image.convert("RGB"))
        report["stress"][relative_path] = await _analyze_image(
            detector,
            recognizer,
            merge,
            pixels,
            target_page=False,
        )

    for relative_path, expected_text in SHORT_TEXT_FIXTURES:
        image_path = args.fixture_root / relative_path
        with Image.open(image_path) as image:
            pixels = np.asarray(image.convert("RGB"))
        report["short_text"][relative_path] = await _analyze_image(
            detector,
            recognizer,
            merge,
            pixels,
            target_page=False,
            expected_short_text=expected_text,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "page9": [
                    {
                        "factor": item["factor"],
                        "regions": item["final_regions"],
                        "target_blocks": item["target_blocks"],
                    }
                    for item in report["page9"]["variants"]
                ]
            },
            sort_keys=True,
        )
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--page9", type=Path, required=True)
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(_main(_parse_args()))
