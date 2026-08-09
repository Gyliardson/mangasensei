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

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "ocr" / "real_manga" / "black_jack"
PAGE201_PATH = "v01/black_jack_v01_pdf201.jpg"
PAGE201_NECKTIE_ZONE = (1040, 1160, 1860, 2000)


@pytest.mark.asyncio
async def test_licensed_page201_necktie_texture_is_not_returned_as_text() -> None:
    """Reject marginal batch-only OCR on the reviewed page-201 necktie pattern."""
    image_path = FIXTURE_ROOT / PAGE201_PATH
    content = image_path.read_bytes()
    with Image.open(image_path) as source:
        dimensions = PageDimensions(width=source.width, height=source.height)

    engine = MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )
    result = await engine.analyze(
        OcrImage(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="image/jpeg",
            dimensions=dimensions,
        )
    )

    necktie_regions = [
        region for region in result.regions if _center_in_zone(region, PAGE201_NECKTIE_ZONE)
    ]
    assert necktie_regions == [], (
        "page-201 necktie texture crossed the recognizer acceptance boundary; "
        f"region_count={len(result.regions)}"
    )


def _center_in_zone(region: OcrRegionResult, zone: tuple[int, int, int, int]) -> bool:
    min_x, max_x, min_y, max_y = zone
    center_x = region.bbox.x + region.bbox.width / 2
    center_y = region.bbox.y + region.bbox.height / 2
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y
