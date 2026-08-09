from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from PIL import Image

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import MangaImageTranslatorEngine
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult

pytestmark = [
    pytest.mark.ocr_smoke,
    pytest.mark.skipif(
        os.environ.get("MANGASENSEI_RUN_OCR_SMOKE") != "1",
        reason="set MANGASENSEI_RUN_OCR_SMOKE=1 to load the real OCR models",
    ),
]


@pytest.mark.asyncio
async def test_licensed_manga_short_vertical_text_recall() -> None:
    """Protect known-good short vertical dialogue without encoding full-page OCR snapshots."""
    engine = MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )
    cases = (
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

    for relative_path, expected_text, expected_center_zone in cases:
        image_path = (
            Path(__file__).parent
            / "fixtures"
            / "ocr"
            / "real_manga"
            / "black_jack"
            / relative_path
        )
        content = image_path.read_bytes()
        with Image.open(image_path) as source:
            dimensions = PageDimensions(width=source.width, height=source.height)

        result = await engine.analyze(
            OcrImage(
                content=content,
                sha256=hashlib.sha256(content).hexdigest(),
                media_type="image/jpeg",
                dimensions=dimensions,
            )
        )
        matches = [region for region in result.regions if expected_text in region.japanese_text]

        assert matches, (
            f"expected short vertical text {expected_text!r} in {relative_path}; "
            f"recognized={[region.japanese_text for region in result.regions]!r}"
        )
        assert 4 <= len(result.regions) <= 32, (
            f"unexpected region-count shift for {relative_path}: {len(result.regions)}"
        )
        assert any(_center_in_zone(region, expected_center_zone) for region in matches), (
            f"{expected_text!r} was recognized outside its reviewed fixture area in {relative_path}"
        )


def _center_in_zone(region: OcrRegionResult, zone: tuple[int, int, int, int]) -> bool:
    min_x, max_x, min_y, max_y = zone
    center_x = region.bbox.x + region.bbox.width / 2
    center_y = region.bbox.y + region.bbox.height / 2
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y
