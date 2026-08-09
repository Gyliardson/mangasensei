from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.infrastructure.database.analysis_models import (
    GeminiCallRecord,
    LinguisticRunRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

_REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"


def _image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class CountingOcr:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, image: OcrImage) -> OcrResult:
        self.calls += 1
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
                    japanese_text="猫です",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class CountingTokenizer:
    def __init__(self) -> None:
        self.calls = 0

    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        self.calls += 1
        assert text == "猫です"
        return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))


class EnglishDictionary:
    version = "JMdict test"
    digest = hashlib.sha256(b"JMdict test").digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        if lemma == "猫":
            return DictionaryEntry(
                id="jmdict-1467640",
                meanings=("cat",),
                source="JMdict test",
                jlpt_level="N5",
                jlpt_official=False,
            )
        return None


class LanguageAwareGemini:
    def __init__(self) -> None:
        self.languages: list[str] = []

    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis:
        payload = json.loads(prompt)
        study_language = payload["study_language"]
        self.languages.append(study_language)
        translation = "É um gato." if study_language == "pt-BR" else "It is a cat."
        explanation = (
            "Frase nominal polida."
            if study_language == "pt-BR"
            else "A polite nominal sentence."
        )
        return schema(
            regions=(
                GeminiRegionAnalysis(
                    region_id=_REGION_ID,
                    translation=translation,
                    explanation=explanation,
                    grammar_points=("です",),
                    vocabulary_ids=("jmdict-1467640",),
                ),
            )
        )


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("study-language-test-pepper-00000001",),
    )


def _worker(
    database_url: str,
    root: Path,
    *,
    ocr: CountingOcr,
    tokenizer: CountingTokenizer,
    gemini: LanguageAwareGemini | None,
) -> tuple[Worker, object]:
    engine, sessions = create_database(database_url)
    worker = Worker(
        sessions=sessions,
        storage=LocalFilesystemStorage(root),
        ocr=ocr,
        linguistics=LinguisticService(tokenizer, EnglishDictionary()),
        gemini=gemini,
        worker_id="study-language-worker",
        lease_seconds=60,
    )
    return worker, engine


@pytest.mark.integration
@pytest.mark.asyncio
async def test_language_only_reprocess_reuses_ocr_and_linguistic_run(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    ocr = CountingOcr()
    tokenizer = CountingTokenizer()
    gemini = LanguageAwareGemini()
    worker, engine = _worker(
        clean_postgres_url,
        tmp_path,
        ocr=ocr,
        tokenizer=tokenizer,
        gemini=gemini,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "study-language-upload-0001"},
            files={"image": ("page.png", _image(), "image/png")},
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]
        assert upload_data["studyLanguage"] == "pt-BR"
        assert await worker.run_once()

        first = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        first_data = first.json()["data"]
        assert first_data["contentLanguage"] == "ja"
        assert first_data["studyLanguage"] == "pt-BR"
        assert first_data["dictionaryLanguage"] == "en"
        assert first_data["regions"][0]["translation"] == "É um gato."
        assert first_data["regions"][0]["vocabulary"][0]["meanings"] == ["cat"]

        reprocess = await client.post(
            f"/api/v1/pages/{upload_data['pageId']}/reprocess",
            headers={
                "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "study-language-reprocess-0001",
            },
            json={"studyLanguage": "en"},
        )
        assert reprocess.status_code == 202
        assert reprocess.json()["data"]["studyLanguage"] == "en"

        while_pending = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        pending_data = while_pending.json()["data"]
        assert pending_data["status"] == "pending"
        assert pending_data["resultAvailable"] is True
        assert pending_data["studyLanguage"] == "pt-BR"

        assert await worker.run_once()

        second = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        second_data = second.json()["data"]
        assert second_data["status"] == "completed"
        assert second_data["contentLanguage"] == "ja"
        assert second_data["studyLanguage"] == "en"
        assert second_data["dictionaryLanguage"] == "en"
        assert second_data["regions"][0]["translation"] == "It is a cat."
        assert second_data["regions"][0]["vocabulary"][0]["meanings"] == ["cat"]

    _, sessions = create_database(clean_postgres_url)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(OcrRunRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(LinguisticRunRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(StudyResultRecord)) == 2
        assert await session.scalar(select(func.count()).select_from(GeminiCallRecord)) == 2
        linguistic_ids = tuple(
            (
                await session.execute(
                    select(StudyResultRecord.linguistic_run_id).order_by(StudyResultRecord.id)
                )
            ).scalars()
        )
        assert linguistic_ids[0] == linguistic_ids[1]
    assert ocr.calls == 1
    assert tokenizer.calls == 1
    assert gemini.languages == ["pt-BR", "en"]
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_english_study_flow_remains_local_when_gemini_is_disabled(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    ocr = CountingOcr()
    tokenizer = CountingTokenizer()
    worker, engine = _worker(
        clean_postgres_url,
        tmp_path,
        ocr=ocr,
        tokenizer=tokenizer,
        gemini=None,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "study-language-local-upload-0001"},
            files={
                "image": ("page.png", _image(), "image/png"),
                "studyLanguage": (None, "en"),
            },
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]
        assert upload_data["studyLanguage"] == "en"
        assert await worker.run_once()

        result = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        data = result.json()["data"]
        assert data["status"] == "completed"
        assert data["studyLanguage"] == "en"
        assert data["regions"][0]["translation"] is None
        assert data["regions"][0]["explanation"] is None
        assert data["regions"][0]["vocabulary"][0]["meanings"] == ["cat"]

        invalid = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "study-language-invalid-upload-0001"},
            files={
                "image": ("page.png", _image(), "image/png"),
                "studyLanguage": (None, "es"),
            },
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "invalid_request"

    assert ocr.calls == 1
    assert tokenizer.calls == 1
    await engine.dispose()  # type: ignore[attr-defined]
