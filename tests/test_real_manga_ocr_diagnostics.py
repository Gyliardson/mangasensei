from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import MangaImageTranslatorEngine, _OcrConfig

pytestmark = [
    pytest.mark.ocr_smoke,
    pytest.mark.skipif(
        os.environ.get("MANGASENSEI_RUN_OCR_DIAGNOSTIC") != "1",
        reason="set MANGASENSEI_RUN_OCR_DIAGNOSTIC=1 for licensed real-manga diagnostics",
    ),
]

_FIXTURES = (
    ("v01/black_jack_v01_pdf073.jpg", "うむ"),
    ("v01/black_jack_v01_pdf090.jpg", "はい"),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("relative_path", "target"), _FIXTURES)
async def test_short_vertical_text_pipeline_stages(relative_path: str, target: str) -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "ocr"
        / "real_manga"
        / "black_jack"
        / relative_path
    )
    with Image.open(fixture) as source:
        import numpy as np

        pixels = np.asarray(source.convert("RGB"))
        height, width = pixels.shape[:2]

    engine = MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )
    detector, recognizer, merge, _ = await engine._ensure_loaded()
    detected, _, _ = await detector.detect(
        pixels,
        2048,
        0.5,
        0.7,
        2.3,
        False,
        False,
        False,
        False,
        False,
    )
    detector_snapshot = [_detector_summary(line) for line in detected]

    production_lines = await recognizer.recognize(
        pixels,
        copy.deepcopy(detected),
        _OcrConfig(prob=0.2),
        False,
    )
    production_summary = [_recognized_summary(line) for line in production_lines]
    production_merged = await merge(copy.deepcopy(production_lines), width, height)
    production_merged_summary = [_recognized_summary(region) for region in production_merged]

    permissive_lines = await recognizer.recognize(
        pixels,
        copy.deepcopy(detected),
        _OcrConfig(prob=0.0),
        False,
    )
    permissive_summary = [_recognized_summary(line) for line in permissive_lines]
    permissive_merged = await merge(copy.deepcopy(permissive_lines), width, height)
    permissive_merged_summary = [_recognized_summary(region) for region in permissive_merged]

    target_in_production = _contains_target(production_summary, target)
    target_in_production_merge = _contains_target(production_merged_summary, target)
    target_in_permissive = _contains_target(permissive_summary, target)
    target_in_permissive_merge = _contains_target(permissive_merged_summary, target)

    print(
        "real_manga_ocr_diagnostic="
        + json.dumps(
            {
                "fixture": relative_path,
                "target": target,
                "dimensions": [width, height],
                "detector_count": len(detector_snapshot),
                "production_recognized_count": len(production_summary),
                "production_merged_count": len(production_merged_summary),
                "permissive_recognized_count": len(permissive_summary),
                "permissive_merged_count": len(permissive_merged_summary),
                "target_in_production": target_in_production,
                "target_in_production_merge": target_in_production_merge,
                "target_in_permissive": target_in_permissive,
                "target_in_permissive_merge": target_in_permissive_merge,
                "detector": detector_snapshot,
                "production": production_summary,
                "production_merged": production_merged_summary,
                "permissive": permissive_summary,
                "permissive_merged": permissive_merged_summary,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    # This first investigation intentionally characterizes the real model boundary
    # rather than changing thresholds. Basic assertions catch fixture/model breakage
    # without pretending that these licensed pages already reproduce the user page.
    assert detector_snapshot
    assert production_summary
    assert permissive_summary
    assert len(permissive_summary) >= len(production_summary)


def _contains_target(regions: list[dict[str, Any]], target: str) -> bool:
    return any(target in str(region["text"]) for region in regions)


def _detector_summary(region: Any) -> dict[str, Any]:
    return {
        "xyxy": [int(value) for value in region.xyxy],
        "direction": str(region.direction),
        "detector_prob": round(float(region.prob), 6),
    }


def _recognized_summary(region: Any) -> dict[str, Any]:
    return {
        "xyxy": [int(value) for value in region.xyxy],
        "text": str(region.text),
        "prob": round(float(region.prob), 6),
    }
