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
from mangasensei.gemini.service import PAGE_STUDY_PROMPT_VERSION
from mangasensei.infrastructure.database.analysis_models import (
    GeminiCallRecord,
    GeminiRegionAnalysisRecord,
    OcrRegionRecord,
)
from mangasensei.infrastructure.database.lexical_models import (
    GeminiLexicalVocabularyLinkRecord,
    LexicalMatchRecord,
)
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

_REGION_CAT = "5ca22b32-6834-59db-a183-428a557a22e8"
_REGION_DOG = "da18fc9f-f905-5043-acf2-02f4c526fd3b"


def settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("gemini-prompt-test-pepper-00000001",),
    )


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (120, 80), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class TwoRegionOcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        dimensions = PageDimensions(width=120, height=80)
        cat_bbox = BoundingBox(x=70, y=10, width=40, height=50)
        dog_bbox = BoundingBox(x=10, y=10, width=40, height=50)
        return OcrResult(
            image_sha256=image.sha256,
            provenance=DEFAULT_FAKE_PROVENANCE,
            regions=(
                OcrRegionResult(
                    id=_REGION_CAT,
                    dimensions=dimensions,
                    bbox=cat_bbox,
                    normalized_bbox=cat_bbox.normalize(dimensions),
                    polygon=((70, 10), (110, 10), (110, 60), (70, 60)),
                    angle=0.0,
                    confidence=0.97,
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
                    polygon=((10, 10), (50, 10), (50, 60), (10, 60)),
                    angle=0.0,
                    confidence=0.96,
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
        raise AssertionError(text)


class TwoRegionDictionaryFixture:
    version = "JMdict prompt mapping test"
    digest = hashlib.sha256(b"JMdict prompt mapping test").digest()

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        del reading
        entry: DictionaryEntry | None = None
        if lemma == "猫":
            entry = DictionaryEntry(
                identity=LexicalFormIdentity("JMdict", "jmdict-cat", "猫", "ねこ"),
                meanings=("cat",),
                source="JMdict prompt fixture",
                jlpt_level="N5",
                jlpt_official=False,
            )
        elif lemma == "犬":
            entry = DictionaryEntry(
                identity=LexicalFormIdentity("JMdict", "jmdict-dog", "犬", "いぬ"),
                meanings=("dog",),
                source="JMdict prompt fixture",
                jlpt_level="N5",
                jlpt_official=False,
            )
        return DictionaryLookupResult.from_candidates((entry,) if entry is not None else ())


class PromptDerivedGeminiFixture:
    def __init__(self) -> None:
        self.prompt: str | None = None

    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis:
        self.prompt = prompt
        payload = json.loads(prompt)
        regions: list[GeminiRegionAnalysis] = []
        for region in payload["regions"]:
            candidates = region["vocabulary_candidates"]
            regions.append(
                GeminiRegionAnalysis(
                    region_id=region["region_id"],
                    translation=f"translation:{candidates[0]['surface']}",
                    explanation=f"explanation:{region['japanese_text']}",
                    grammar_points=("です",),
                    vocabulary_ids=(candidates[0]["id"],),
                )
            )
        return schema(regions=tuple(regions))


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
                    select(
                        OcrRegionRecord.public_id,
                        LexicalMatchRecord.dictionary_entry_id,
                        LexicalMatchRecord.form_lemma,
                        LexicalMatchRecord.form_reading,
                    )
                    .select_from(GeminiLexicalVocabularyLinkRecord)
                    .join(
                        GeminiRegionAnalysisRecord,
                        GeminiRegionAnalysisRecord.id
                        == GeminiLexicalVocabularyLinkRecord.region_analysis_id,
                    )
                    .join(
                        OcrRegionRecord,
                        OcrRegionRecord.id == GeminiRegionAnalysisRecord.region_id,
                    )
                    .join(
                        LexicalMatchRecord,
                        LexicalMatchRecord.id
                        == GeminiLexicalVocabularyLinkRecord.lexical_match_id,
                    )
                    .order_by(OcrRegionRecord.region_ordinal)
                )
            ).all()

        assert call.prompt_version == PAGE_STUDY_PROMPT_VERSION
        assert call.request_digest == hashlib.sha256(gemini.prompt.encode()).digest()
        assert links == [
            (UUID(_REGION_CAT), "jmdict-cat", "猫", "ねこ"),
            (UUID(_REGION_DOG), "jmdict-dog", "犬", "いぬ"),
        ]
        await engine.dispose()
