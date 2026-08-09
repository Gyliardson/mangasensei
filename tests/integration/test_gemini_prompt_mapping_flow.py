from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.infrastructure.database.analysis_models import (
    GeminiCallRecord,
    GeminiRegionAnalysisRecord,
    GeminiVocabularyLinkRecord,
    LinguisticTokenRecord,
    OcrRegionRecord,
)
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

_REGION_CAT = "5ca22b32-6834-59db-a183-428a557a22e8"
_REGION_DOG = "08aaae95-00b4-5f4e-b02e-9b79e31b7f84"


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class TwoRegionOcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        dimensions = PageDimensions(width=80, height=120)
        cat_bbox = BoundingBox(x=10, y=10, width=40, height=40)
        dog_bbox = BoundingBox(x=10, y=65, width=40, height=40)
        return OcrResult(
            image_sha256=image.sha256,
            provenance=DEFAULT_FAKE_PROVENANCE,
            regions=(
                OcrRegionResult(
                    id=_REGION_CAT,
                    dimensions=dimensions,
                    bbox=cat_bbox,
                    normalized_bbox=cat_bbox.normalize(dimensions),
                    polygon=(),
                    angle=0.0,
                    confidence=0.98,
                    japanese_text="猫です",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
                OcrRegionResult(
                    id=_REGION_DOG,
                    dimensions=dimensions,
                    bbox=dog_bbox,
                    normalized_bbox=dog_bbox.normalize(dimensions),
                    polygon=(),
                    angle=0.0,
                    confidence=0.97,
                    japanese_text="犬です",
                    reading_order=1,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class TwoRegionTokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        if text == "猫です":
            return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))
        if text == "犬です":
            return (("犬", "犬", "イヌ", "名詞"), ("です", "です", "デス", "助動詞"))
        raise AssertionError(f"unexpected OCR text: {text}")


class TwoRegionDictionaryFixture:
    version = "JMdict prompt mapping fixture"
    digest = hashlib.sha256(version.encode()).digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        entries = {
            ("猫", "ネコ"): DictionaryEntry(
                id="jmdict-cat",
                meanings=("cat",),
                source="JMdict fixture",
                jlpt_level="N5",
                jlpt_official=False,
            ),
            ("犬", "イヌ"): DictionaryEntry(
                id="jmdict-dog",
                meanings=("dog",),
                source="JMdict fixture",
                jlpt_level="N5",
                jlpt_official=False,
            ),
        }
        return entries.get((lemma, reading))


class PromptDerivedGeminiFixture:
    def __init__(self) -> None:
        self.prompt: str | None = None

    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        self.prompt = prompt
        payload = json.loads(prompt)
        analyses: list[GeminiRegionAnalysis] = []
        for region in payload["regions"]:
            candidates = region["vocabulary_candidates"]
            assert len(candidates) == 1
            candidate = candidates[0]
            assert candidate["surface"] in region["japanese_text"]
            analyses.append(
                GeminiRegionAnalysis(
                    region_id=region["region_id"],
                    translation=f"translation:{candidate['surface']}",
                    explanation="Derived only from the region-scoped prompt candidate.",
                    grammar_points=("です",),
                    vocabulary_ids=(candidate["id"],),
                )
            )
        return schema(regions=tuple(analyses))


def settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("gemini-prompt-mapping-pepper-0001",),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_gemini_links_are_derived_from_region_scoped_prompt(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    gemini = PromptDerivedGeminiFixture()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "gemini-prompt-mapping-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=TwoRegionOcrFixture(),
            linguistics=LinguisticService(
                TwoRegionTokenizerFixture(), TwoRegionDictionaryFixture()
            ),
            gemini=gemini,
            worker_id="gemini-prompt-mapping-worker",
            lease_seconds=60,
        )
        assert await worker.run_once()
        assert gemini.prompt is not None

        result = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert result.status_code == 200
        regions = result.json()["data"]["regions"]
        assert [(region["text"], region["translation"]) for region in regions] == [
            ("猫です", "translation:猫"),
            ("犬です", "translation:犬"),
        ]

        async with sessions() as session:
            call = (await session.execute(select(GeminiCallRecord))).scalar_one()
            links = (
                await session.execute(
                    select(OcrRegionRecord.public_id, LinguisticTokenRecord.dictionary_entry_id)
                    .select_from(GeminiVocabularyLinkRecord)
                    .join(
                        GeminiRegionAnalysisRecord,
                        GeminiRegionAnalysisRecord.id
                        == GeminiVocabularyLinkRecord.region_analysis_id,
                    )
                    .join(
                        OcrRegionRecord,
                        OcrRegionRecord.id == GeminiRegionAnalysisRecord.region_id,
                    )
                    .join(
                        LinguisticTokenRecord,
                        LinguisticTokenRecord.id == GeminiVocabularyLinkRecord.token_id,
                    )
                    .order_by(OcrRegionRecord.region_ordinal)
                )
            ).all()

        assert call.prompt_version == "page-study-v2"
        assert call.request_digest == hashlib.sha256(gemini.prompt.encode()).digest()
        assert links == [
            (UUID(_REGION_CAT), "jmdict-cat"),
            (UUID(_REGION_DOG), "jmdict-dog"),
        ]
        await engine.dispose()
