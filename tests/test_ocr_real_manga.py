from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import MangaImageTranslatorEngine
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult

pytestmark = [
    pytest.mark.ocr_smoke,
    pytest.mark.skipif(
        os.environ.get("MANGASENSEI_RUN_OCR_SMOKE") != "1",
        reason="set MANGASENSEI_RUN_OCR_SMOKE=1 to load the real OCR models",
    ),
]

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "ocr" / "real_manga" / "black_jack"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
SHORT_TEXT_CASES = (
    (
        "v01/black_jack_v01_pdf073.jpg",
        "うむ",
        (500, 620, 250, 420),
    ),
    (
        "v01/black_jack_v01_pdf090.jpg",
        "はい",
        (1030, 1210, 160, 480),
    ),
)
SHORT_TEXT_PATHS = {case[0] for case in SHORT_TEXT_CASES}


@pytest.mark.asyncio
async def test_licensed_manga_short_vertical_text_recall_is_repeatable() -> None:
    """Protect reviewed short-text recall across repeated identical CPU inference."""
    engine = _real_engine()
    repeat_runs = _repeat_runs()

    for relative_path, expected_text, expected_center_zone in SHORT_TEXT_CASES:
        observations: list[tuple[int, tuple[tuple[float, float], ...]]] = []
        for _ in range(repeat_runs):
            result = await _analyze_fixture(engine, relative_path)
            matches = [region for region in result.regions if expected_text in region.japanese_text]
            in_zone = tuple(
                _center(region)
                for region in matches
                if _center_in_zone(region, expected_center_zone)
            )

            assert matches, (
                f"expected reviewed short vertical target in {relative_path}; "
                f"region_count={len(result.regions)}"
            )
            assert 4 <= len(result.regions) <= 32, (
                f"unexpected region-count shift for {relative_path}: {len(result.regions)}"
            )
            assert in_zone, (
                f"reviewed short target was recognized outside its fixture area in {relative_path}"
            )
            observations.append((len(result.regions), in_zone))

        print(
            "OCR_REPEATABILITY "
            f"fixture={relative_path} runs={repeat_runs} observations={observations!r}"
        )


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("MANGASENSEI_OCR_FULL_CORPUS") != "1",
    reason="full licensed corpus is reserved for the deeper OCR assurance tier",
)
async def test_licensed_manga_full_corpus_characterization() -> None:
    """Characterize non-anchor corpus pages without inventing transcript ground truth."""
    engine = _real_engine()
    manifest = _load_manifest()

    for fixture in manifest["fixtures"]:
        relative_path = _manifest_string(fixture, "file")
        if relative_path in SHORT_TEXT_PATHS:
            continue

        result = await _analyze_fixture(engine, relative_path)
        assert result.regions, f"OCR detection collapsed to zero regions for {relative_path}"
        print(f"OCR_CORPUS fixture={relative_path} region_count={len(result.regions)}")


def _real_engine() -> MangaImageTranslatorEngine:
    return MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )


async def _analyze_fixture(
    engine: MangaImageTranslatorEngine,
    relative_path: str,
) -> OcrResult:
    image_path = FIXTURE_ROOT / relative_path
    content = image_path.read_bytes()
    with Image.open(image_path) as source:
        dimensions = PageDimensions(width=source.width, height=source.height)

    return await engine.analyze(
        OcrImage(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="image/jpeg",
            dimensions=dimensions,
        )
    )


def _repeat_runs() -> int:
    value = int(os.environ.get("MANGASENSEI_OCR_REPEAT_RUNS", "1"))
    if not 1 <= value <= 5:
        raise ValueError("MANGASENSEI_OCR_REPEAT_RUNS must be between 1 and 5")
    return value


def _load_manifest() -> dict[str, Any]:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    fixtures = raw.get("fixtures")
    assert isinstance(fixtures, list)
    return raw


def _manifest_string(entry: object, key: str) -> str:
    assert isinstance(entry, dict)
    value = entry[key]
    assert isinstance(value, str)
    return value


def _center(region: OcrRegionResult) -> tuple[float, float]:
    return (
        region.bbox.x + region.bbox.width / 2,
        region.bbox.y + region.bbox.height / 2,
    )


def _center_in_zone(region: OcrRegionResult, zone: tuple[int, int, int, int]) -> bool:
    min_x, max_x, min_y, max_y = zone
    center_x, center_y = _center(region)
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y
