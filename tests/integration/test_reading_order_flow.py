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
from mangasensei.linguistics.service import DictionaryLookupResult, LinguisticService
from mangasensei.ocr.contracts import OcrRegionResult
from mangasensei.ocr.fake import FakeOcrEngine
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker


class TokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        return ((text, text, text, "名詞"),)


class DictionaryFixture:
    version = "reading-order-test"
    digest = hashlib.sha256(version.encode()).digest()

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        del lemma, reading
        return DictionaryLookupResult.from_candidates(())


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (100, 100), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


def region(
    *,
    public_id: str,
    text: str,
    reading_order: int,
    bbox: BoundingBox,
) -> OcrRegionResult:
    dimensions = PageDimensions(width=100, height=100)
    return OcrRegionResult(
        id=public_id,
        dimensions=dimensions,
        bbox=bbox,
        normalized_bbox=bbox.normalize(dimensions),
        polygon=None,
        angle=0.0,
        confidence=0.95,
        japanese_text=text,
        reading_order=reading_order,
        detector="reading-order-fixture",
        recognizer="reading-order-fixture",
        upstream_commit="reading-order-fixture-v1",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_page_api_returns_regions_by_persisted_reading_order(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = Settings(
        environment="test",
        database_url=clean_postgres_url,
        storage_root=tmp_path,
        capability_peppers=("reading-order-flow-pepper-value-000001",),
    )
    app = create_app(application_settings)
    top_right = region(
        public_id="5ca22b32-6834-59db-a183-428a557a22e8",
        text="上右",
        reading_order=0,
        bbox=BoundingBox(x=70, y=10, width=20, height=30),
    )
    top_left = region(
        public_id="08aaae95-00b4-5f4e-b02e-9b79e31b7f84",
        text="上左",
        reading_order=1,
        bbox=BoundingBox(x=20, y=12, width=20, height=30),
    )
    lower_right = region(
        public_id="12e4da9c-8cf5-566b-b768-4f6346c1d71f",
        text="下右",
        reading_order=2,
        bbox=BoundingBox(x=72, y=60, width=20, height=30),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "reading-order-flow-a"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            # Deliberately persist tuple/region ordinals in a different order.
            ocr=FakeOcrEngine(regions=(lower_right, top_left, top_right)),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=None,
            worker_id="reading-order-worker",
            lease_seconds=60,
        )
        try:
            assert await worker.run_once()
            response = await client.get(
                f"/api/v1/pages/{upload_data['pageId']}",
                headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert [item["text"] for item in data["regions"]] == ["上右", "上左", "下右"]
            assert [item["readingOrder"] for item in data["regions"]] == [0, 1, 2]
        finally:
            await engine.dispose()
