"""Audit low-confidence recognizer outputs for batch-vs-isolated stability."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
)

FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "tests"
    / "fixtures"
    / "ocr"
    / "real_manga"
    / "black_jack"
)
MANIFEST = FIXTURE_ROOT / "manifest.json"
LOW_CONFIDENCE_CEILING = 0.5


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, default=Path("var/models"))
    return parser.parse_args()


def _geometry_key(line: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(line.pts))


def _recognized_map(lines: list[Any]) -> dict[tuple[tuple[int, int], ...], dict[str, Any]]:
    return {
        _geometry_key(line): {
            "text": str(line.text),
            "confidence": float(line.prob),
        }
        for line in lines
        if str(line.text).strip()
    }


async def _run() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, _, _ = await engine._ensure_loaded()

    results: list[dict[str, Any]] = []
    total_detector = 0
    total_recognized = 0
    low_confidence_count = 0
    isolated_rejected = 0
    isolated_text_changed = 0

    for fixture in manifest["fixtures"]:
        relative_path = str(fixture["file"])
        label = Path(relative_path).stem
        with Image.open(FIXTURE_ROOT / relative_path) as source:
            pixels = np.asarray(source.convert("RGB"))
        textlines, _, _ = await detector.detect(
            pixels,
            engine._detection_size,
            engine._text_threshold,
            engine._box_threshold,
            engine._unclip_ratio,
            *_DETECTOR_FLAGS,
        )
        total_detector += len(textlines)
        full = await recognizer.recognize(
            pixels,
            copy.deepcopy(textlines),
            engine._ocr_config,
            _RECOGNIZER_FLAG,
        )
        total_recognized += len(full)
        full_map = _recognized_map(full)

        for index, detector_line in enumerate(textlines):
            key = _geometry_key(detector_line)
            batch = full_map.get(key)
            if batch is None or float(batch["confidence"]) >= LOW_CONFIDENCE_CEILING:
                continue
            low_confidence_count += 1
            isolated_lines = await recognizer.recognize(
                pixels,
                [copy.deepcopy(detector_line)],
                engine._ocr_config,
                _RECOGNIZER_FLAG,
            )
            isolated = _recognized_map(isolated_lines).get(key)
            if isolated is None:
                isolated_rejected += 1
            elif str(isolated["text"]) != str(batch["text"]):
                isolated_text_changed += 1
            results.append(
                {
                    "page": label,
                    "index": index,
                    "xyxy": [int(value) for value in detector_line.xyxy],
                    "direction": str(detector_line.direction),
                    "font_size": float(detector_line.font_size),
                    "detector_probability": float(detector_line.prob),
                    "batch": batch,
                    "isolated": isolated,
                }
            )

    payload = {
        "schema_version": 1,
        "low_confidence_ceiling": LOW_CONFIDENCE_CEILING,
        "detector_count": total_detector,
        "recognized_count": total_recognized,
        "low_confidence_count": low_confidence_count,
        "isolated_rejected": isolated_rejected,
        "isolated_text_changed": isolated_text_changed,
        "results": results,
    }
    (output / "low-confidence-isolation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_LOW_CONF_ISOLATION "
        f"detector={total_detector} recognized={total_recognized} low={low_confidence_count} "
        f"isolated_rejected={isolated_rejected} text_changed={isolated_text_changed}"
    )


if __name__ == "__main__":
    asyncio.run(_run())
