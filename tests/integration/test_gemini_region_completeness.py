from __future__ import annotations

import hashlib
import io
from decimal import Decimal
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
    GeminiAnalysisRecord,
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
    GeminiCostLedgerRecord,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

_REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"
_RESERVATION = Decimal("0.52")


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class OneRegionOcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        dimensions = PageDimensions(width=80, height=120)
        bbox = BoundingBox(x=10, y=20, width=40, height=60)
        return OcrResult(
            image_sha256=image.sha256,
            regions=(
                OcrRegionResult(
                    id=_REGION_ID,
                    dimensions=dimensions,
                    bbox=bbox,
                    normalized_bbox=bbox.normalize(dimensions),
                    polygon=(),
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


class EmptyOcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        return OcrResult(image_sha256=image.sha256, regions=())


class TokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "猫です"
        return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))


class DictionaryFixture:
    version = "JMdict completeness fixture"
    digest = hashlib.sha256(version.encode()).digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        if (lemma, reading) == ("猫", "ネコ"):
            return DictionaryEntry(
                id="jmdict-cat",
                meanings=("cat",),
                source="JMdict fixture",
                jlpt_level="N5",
                jlpt_official=False,
            )
        return None


class MalformedGeminiFixture:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        self.calls += 1
        assert "猫です" in prompt
        region = GeminiRegionAnalysis(
            region_id=_REGION_ID,
            translation="Cat.",
            explanation="Malformed cardinality fixture.",
            grammar_points=(),
            vocabulary_ids=("jmdict-cat",),
        )
        if self.mode == "missing":
            return schema(regions=())
        if self.mode == "duplicate":
            return schema(regions=(region, region))
        raise AssertionError(f"unexpected mode: {self.mode}")


class NeverCalledGeminiFixture:
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        del prompt, schema
        self.calls += 1
        raise AssertionError("Gemini must not be called when OCR has no regions")


def settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("gemini-completeness-pepper-0001",),
    )


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "duplicate"])
async def test_malformed_gemini_regions_retry_without_partial_analysis(
    clean_postgres_url: str,
    tmp_path: Path,
    mode: str,
) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    gemini = MalformedGeminiFixture(mode)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": f"gemini-completeness-{mode}-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=OneRegionOcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=gemini,
            worker_id=f"gemini-completeness-{mode}-worker",
            lease_seconds=60,
        )
        assert await worker.run_once()
        assert gemini.calls == 1

        async with sessions() as session:
            job = (
                await session.execute(
                    select(JobRecord).where(JobRecord.public_id == UUID(upload_data["jobId"]))
                )
            ).scalar_one()
            attempt = (
                await session.execute(
                    select(JobAttemptRecord).where(JobAttemptRecord.job_id == job.id)
                )
            ).scalar_one()
            call = (await session.execute(select(GeminiCallRecord))).scalar_one()
            bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()
            ledger = (
                await session.execute(select(GeminiCostLedgerRecord))
            ).scalars().all()
            analyses = (
                await session.execute(select(GeminiAnalysisRecord))
            ).scalars().all()

        assert job.status == "retryable_failure"
        assert job.error_code == "gemini_response_invalid"
        assert attempt.outcome == "retryable_failure"
        assert call.state == "unknown"
        assert bucket.reserved_amount == Decimal("0")
        assert bucket.actual_amount == _RESERVATION
        assert len(ledger) == 1
        assert ledger[0].usage_category == "unknown_upper_bound"
        assert ledger[0].amount == _RESERVATION
        assert analyses == []

        page = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert page.status_code == 200
        assert page.json()["data"]["status"] == "retryable_failure"
        assert page.json()["data"]["resultAvailable"] is False
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_zero_ocr_regions_complete_without_gemini_call(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    gemini = NeverCalledGeminiFixture()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "gemini-completeness-empty-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=EmptyOcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=gemini,
            worker_id="gemini-completeness-empty-worker",
            lease_seconds=60,
        )
        assert await worker.run_once()
        assert gemini.calls == 0

        async with sessions() as session:
            job = (
                await session.execute(
                    select(JobRecord).where(JobRecord.public_id == UUID(upload_data["jobId"]))
                )
            ).scalar_one()
            attempt = (
                await session.execute(
                    select(JobAttemptRecord).where(JobAttemptRecord.job_id == job.id)
                )
            ).scalar_one()
            calls = (await session.execute(select(GeminiCallRecord))).scalars().all()
            buckets = (
                await session.execute(select(GeminiBudgetBucketRecord))
            ).scalars().all()

        assert job.status == "completed"
        assert attempt.outcome == "completed_without_gemini"
        assert calls == []
        assert buckets == []

        page = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert page.status_code == 200
        data = page.json()["data"]
        assert data["status"] == "completed"
        assert data["resultAvailable"] is True
        assert data["regions"] == []
        await engine.dispose()
