from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.linguistics.sudachi import SudachiTokenizer
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker


def _fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class _CompatibilityOcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        dimensions = PageDimensions(width=80, height=120)
        bbox = BoundingBox(x=10, y=20, width=40, height=60)
        return OcrResult(
            image_sha256=image.sha256,
            regions=(
                OcrRegionResult(
                    id="5ca22b32-6834-59db-a183-428a557a22e8",
                    dimensions=dimensions,
                    bbox=bbox,
                    normalized_bbox=bbox.normalize(dimensions),
                    polygon=((10, 20), (50, 20), (50, 80), (10, 80)),
                    angle=0.0,
                    confidence=0.97,
                    japanese_text="㈱",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class _NoMatchDictionary:
    version = "JMdict test"
    digest = hashlib.sha256(b"JMdict test").digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        return None


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("sudachi-regression-pepper-value-000001",),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_completes_without_gemini_when_sudachi_emits_zero_width_morphemes(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = _settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "sudachi-zero-width-regression-0001"},
            files={"image": ("page.png", _fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=_CompatibilityOcrFixture(),
            linguistics=LinguisticService(SudachiTokenizer(), _NoMatchDictionary()),
            gemini=None,
            worker_id="sudachi-regression-worker",
            lease_seconds=60,
        )

        assert await worker.run_once()

        result = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert result.status_code == 200
        data = result.json()["data"]
        assert data["status"] == "completed"
        tokens = data["regions"][0]["tokens"]
        assert tokens
        assert all(token["surface"] for token in tokens)
        assert "".join(token["surface"] for token in tokens) == "㈱"
        await engine.dispose()
