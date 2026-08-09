from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import MangaImageTranslatorEngine
from mangasensei.ocr.contracts import OcrImage
from mangasensei.ocr.models.manifest import ModelManifest

pytestmark = [
    pytest.mark.ocr_smoke,
    pytest.mark.skipif(
        os.environ.get("MANGASENSEI_RUN_OCR_SMOKE") != "1",
        reason="set MANGASENSEI_RUN_OCR_SMOKE=1 to load the real OCR models",
    ),
]


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
