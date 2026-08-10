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
from mangasensei.linguistics.service import (
    DictionaryEntry,
    DictionaryLookupResult,
    LexicalFormIdentity,
    LinguisticService,
)
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

_REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"
_SHARED_ENTRY_ID = "jmdict-shared-form-fixture"


def _fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class _OcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        dimensions = PageDimensions(width=80, height=120)
        bbox = BoundingBox(x=10, y=20, width=40, height=60)
        return OcrResult(
            image_sha256=image.sha256,
            provenance=DEFAULT_FAKE_PROVENANCE,
            regions=(
                OcrRegionResult(
                    id=_REGION_ID,
                    dimensions=dimensions,
                    bbox=bbox,
                    normalized_bbox=bbox.normalize(dimensions),
                    polygon=((10, 20), (50, 20), (50, 80), (10, 80)),
                    angle=0.0,
                    confidence=0.97,
                    japanese_text="甲乙",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class _TokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "甲乙"
        return (
            ("甲", "甲", "コウ", "名詞"),
            ("乙", "乙", "オツ", "名詞"),
        )


class _DictionaryFixture:
    version = "JMdict form identity fixture"
    digest = hashlib.sha256(version.encode()).digest()

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        del reading
        entries = {
            "甲": DictionaryEntry(
                identity=LexicalFormIdentity("JMdict", _SHARED_ENTRY_ID, "甲", "こう"),
                meanings=("first canonical form",),
                source=self.version,
                jlpt_level=None,
                jlpt_official=False,
            ),
            "乙": DictionaryEntry(
                identity=LexicalFormIdentity("JMdict", _SHARED_ENTRY_ID, "乙", "おつ"),
                meanings=("second canonical form",),
                source=self.version,
                jlpt_level=None,
                jlpt_official=False,
            ),
        }
        entry = entries.get(lemma)
        return DictionaryLookupResult.from_candidates((entry,) if entry is not None else ())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_page_query_preserves_distinct_canonical_forms_sharing_one_entry_id(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        database_url=clean_postgres_url,
        storage_root=tmp_path,
        capability_peppers=("lexical-form-projection-pepper-00000001",),
    )
    app = create_app(settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "lexical-form-projection-0001"},
            files={"image": ("page.png", _fixture_image(), "image/png")},
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=_OcrFixture(),
            linguistics=LinguisticService(_TokenizerFixture(), _DictionaryFixture()),
            gemini=None,
            worker_id="lexical-form-projection-worker",
            lease_seconds=60,
        )
        try:
            assert await worker.run_once()
            response = await client.get(
                f"/api/v1/pages/{upload_data['pageId']}",
                headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
            )
        finally:
            await engine.dispose()

    assert response.status_code == 200
    region = response.json()["data"]["regions"][0]
    assert [item["id"] for item in region["vocabulary"]] == [
        _SHARED_ENTRY_ID,
        _SHARED_ENTRY_ID,
    ]
    assert [item["lemma"] for item in region["vocabulary"]] == ["甲", "乙"]
    assert [item["meanings"] for item in region["vocabulary"]] == [
        ["first canonical form"],
        ["second canonical form"],
    ]
    assert [token["dictionaryId"] for token in region["tokens"]] == [
        _SHARED_ENTRY_ID,
        _SHARED_ENTRY_ID,
    ]
