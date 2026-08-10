from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import MangaImageTranslatorEngine
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult
from mangasensei.ocr.models.manifest import ModelManifest

pytestmark = [
    pytest.mark.ocr_smoke,
    pytest.mark.skipif(
        os.environ.get("MANGASENSEI_RUN_OCR_SMOKE") != "1",
        reason="set MANGASENSEI_RUN_OCR_SMOKE=1 to load the real OCR models",
    ),
]

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "ocr" / "real_manga" / "black_jack"
_PAGE21_PATH = "v01/black_jack_v01_pdf021.jpg"


@pytest.mark.asyncio
async def test_real_models_load_and_complete_cpu_inference() -> None:
    image = Image.new("RGB", (800, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 760, 560), outline="black", width=12)
    draw.ellipse((160, 180, 640, 420), outline="black", width=8)
    draw.rectangle((40, 620, 760, 1160), outline="black", width=12)
    output = io.BytesIO()
    image.save(output, format="PNG")
    content = output.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    manifest = ModelManifest.load(
        Path(__file__).parents[1]
        / "backend"
        / "src"
        / "mangasensei"
        / "ocr"
        / "models"
        / "manifest.json"
    )

    result = await MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
        detection_size=1024,
    ).analyze(
        OcrImage(
            content=content,
            sha256=digest,
            media_type="image/png",
            dimensions=PageDimensions(width=800, height=1200),
        )
    )

    assert result.image_sha256 == digest
    assert result.provenance.model_manifest_version == manifest.version
    assert result.provenance.upstream_commit == manifest.upstream_commit
    assert len(result.provenance.config_digest) == 32
    assert isinstance(result.regions, tuple)


@pytest.mark.asyncio
async def test_licensed_page21_orders_offset_neighboring_panels_by_panel_flow() -> None:
    image_path = _FIXTURE_ROOT / _PAGE21_PATH
    content = image_path.read_bytes()
    with Image.open(image_path) as source:
        dimensions = PageDimensions(width=source.width, height=source.height)

    result = await MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    ).analyze(
        OcrImage(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="image/jpeg",
            dimensions=dimensions,
        )
    )

    labels = [_page21_panel_label(region) for region in result.regions]
    upper_right = [index for index, label in enumerate(labels) if label == "upper-right"]
    upper_left = [index for index, label in enumerate(labels) if label == "upper-left"]
    lower_right = [index for index, label in enumerate(labels) if label == "lower-right"]
    lower_left = [index for index, label in enumerate(labels) if label == "lower-left"]

    assert upper_right and upper_left, labels
    assert max(upper_right) < min(upper_left), labels
    assert lower_right and lower_left, labels
    assert max(lower_right) < min(lower_left), labels


def _page21_panel_label(region: OcrRegionResult) -> str:
    center_x = region.bbox.x + region.bbox.width / 2
    center_y = region.bbox.y + region.bbox.height / 2
    if center_y < 820 and center_x >= 700:
        return "upper-right"
    if center_y < 820:
        return "upper-left"
    if center_y < 1330:
        return "middle"
    if center_x >= 844:
        return "lower-right"
    if center_x >= 535:
        return "lower-middle"
    return "lower-left"
