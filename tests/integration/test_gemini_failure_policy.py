from __future__ import annotations

import hashlib
import io
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.gemini.errors import GeminiProviderError, GeminiProviderFailureKind
from mangasensei.infrastructure.database.analysis_models import (
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

_REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"


def _fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class OcrFixture:
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
                    japanese_text="猫です",
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class TokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        del text
        return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))


class DictionaryFixture:
    version = "JMdict test"
    digest = hashlib.sha256(b"JMdict test").digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        del reading
        if lemma == "猫":
            return DictionaryEntry(
                id="jmdict-1467640",
                meanings=("cat",),
                source="JMdict test",
                jlpt_level="N5",
                jlpt_official=False,
            )
        return None


class SuccessfulGeminiFixture:
    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis:
        assert _REGION_ID in prompt
        return schema(
            regions=(
                GeminiRegionAnalysis(
                    region_id=_REGION_ID,
                    translation="Synthetic translation.",
                    explanation="Synthetic explanation.",
                    grammar_points=("synthetic grammar",),
                    vocabulary_ids=("jmdict-1467640",),
                ),
            )
        )


class PermanentProviderFailureFixture:
    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis:
        del prompt, schema
        raise GeminiProviderError(
            kind=GeminiProviderFailureKind.REQUEST,
            retryable=False,
            status_code=400,
        )


class TransientProviderFailureFixture(SuccessfulGeminiFixture):
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis:
        self.calls += 1
        if self.calls == 1:
            raise GeminiProviderError(
                kind=GeminiProviderFailureKind.SERVER,
                retryable=True,
                status_code=503,
            )
        return await super().analyze(prompt=prompt, schema=schema)


class FailBeforeSentWorker(Worker):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.fail_once = True

    async def _mark_call_sent(self, call_id: int) -> None:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("synthetic failure before provider send")
        await super()._mark_call_sent(call_id)


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("gemini-policy-pepper-value-00000001",),
    )


def _worker_kwargs(
    sessions: async_sessionmaker[AsyncSession], root: Path, gemini: object
) -> dict[str, Any]:
    return {
        "sessions": sessions,
        "storage": LocalFilesystemStorage(root),
        "ocr": OcrFixture(),
        "linguistics": LinguisticService(TokenizerFixture(), DictionaryFixture()),
        "gemini": gemini,
        "worker_id": "gemini-policy-worker",
        "lease_seconds": 60,
    }


async def _upload_page(client: AsyncClient, key: str) -> dict[str, object]:
    response = await client.post(
        "/api/v1/pages",
        headers={"Idempotency-Key": key},
        files={"image": ("page.png", _fixture_image(), "image/png")},
    )
    assert response.status_code == 202
    return response.json()["data"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_permanent_provider_failure_is_terminal_after_one_job_attempt(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await _upload_page(client, "gemini-permanent-failure-0001")

    engine, sessions = create_database(clean_postgres_url)
    worker = Worker(**_worker_kwargs(sessions, tmp_path, PermanentProviderFailureFixture()))
    assert await worker.run_once()
    assert await worker.run_once() is False

    async with sessions() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        attempts = (
            await session.execute(
                select(JobAttemptRecord)
                .where(JobAttemptRecord.job_id == job.id)
                .order_by(JobAttemptRecord.attempt_no)
            )
        ).scalars().all()
        calls = (await session.execute(select(GeminiCallRecord))).scalars().all()
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()

    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.error_code == "gemini_provider_failed"
    assert [attempt.outcome for attempt in attempts] == ["failed"]
    assert len(calls) == 1
    assert calls[0].state == "unknown"
    assert calls[0].page_id == job.page_id
    assert calls[0].page_call_ordinal == 1
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == Decimal("0.52")
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_transient_provider_failure_remains_retryable_and_can_succeed(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await _upload_page(client, "gemini-transient-failure-0001")

    engine, sessions = create_database(clean_postgres_url)
    gemini = TransientProviderFailureFixture()
    worker = Worker(**_worker_kwargs(sessions, tmp_path, gemini))

    assert await worker.run_once()
    async with sessions.begin() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        assert job.status == "retryable_failure"
        assert job.error_code == "gemini_provider_failed"
        job.available_at = func.now()

    assert await worker.run_once()

    async with sessions() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        attempts = (
            await session.execute(
                select(JobAttemptRecord)
                .where(JobAttemptRecord.job_id == job.id)
                .order_by(JobAttemptRecord.attempt_no)
            )
        ).scalars().all()
        calls = (
            await session.execute(select(GeminiCallRecord).order_by(GeminiCallRecord.id))
        ).scalars().all()
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()

    assert job.status == "completed"
    assert [attempt.outcome for attempt in attempts] == ["retryable_failure", "completed"]
    assert [call.state for call in calls] == ["unknown", "succeeded"]
    assert [call.page_call_ordinal for call in calls] == [1, 2]
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == Decimal("1.04")
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_daily_budget_exhaustion_is_terminal_without_creating_a_call(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await _upload_page(client, "gemini-daily-budget-0001")

    engine, sessions = create_database(clean_postgres_url)
    worker = Worker(
        **_worker_kwargs(sessions, tmp_path, SuccessfulGeminiFixture()),
        gemini_daily_budget=Decimal("0.50"),
    )

    assert await worker.run_once()
    assert await worker.run_once() is False

    async with sessions() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        calls = (await session.execute(select(GeminiCallRecord))).scalars().all()
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()

    assert job.status == "failed"
    assert job.attempt_count == 1
    assert job.error_code == "gemini_budget_exceeded"
    assert calls == []
    assert bucket.limit_amount == Decimal("0.50")
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == Decimal("0")
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_page_call_limit_is_terminal_after_a_transient_sent_call(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await _upload_page(client, "gemini-page-call-limit-0001")

    engine, sessions = create_database(clean_postgres_url)
    worker = Worker(
        **_worker_kwargs(sessions, tmp_path, TransientProviderFailureFixture()),
        gemini_daily_budget=Decimal("100"),
        gemini_max_calls_per_page=1,
    )

    assert await worker.run_once()
    async with sessions.begin() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        assert job.status == "retryable_failure"
        job.available_at = func.now()

    assert await worker.run_once()
    assert await worker.run_once() is False

    async with sessions() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        attempts = (
            await session.execute(
                select(JobAttemptRecord)
                .where(JobAttemptRecord.job_id == job.id)
                .order_by(JobAttemptRecord.attempt_no)
            )
        ).scalars().all()
        calls = (await session.execute(select(GeminiCallRecord))).scalars().all()
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()

    assert job.status == "failed"
    assert job.attempt_count == 2
    assert job.error_code == "gemini_budget_exceeded"
    assert [attempt.outcome for attempt in attempts] == ["retryable_failure", "failed"]
    assert len(calls) == 1
    assert calls[0].state == "unknown"
    assert calls[0].page_call_ordinal == 1
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == Decimal("0.52")
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unsent_reservation_releases_budget_and_page_ordinal(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await _upload_page(client, "gemini-unsent-reservation-0001")

    engine, sessions = create_database(clean_postgres_url)
    worker = FailBeforeSentWorker(
        **_worker_kwargs(sessions, tmp_path, SuccessfulGeminiFixture())
    )

    assert await worker.run_once()
    async with sessions.begin() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        assert job.status == "retryable_failure"
        job.available_at = func.now()

    async with sessions() as session:
        first_call = (await session.execute(select(GeminiCallRecord))).scalar_one()
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()
        assert first_call.state == "failed"
        assert first_call.page_id is None
        assert first_call.page_call_ordinal == 1
        assert bucket.reserved_amount == Decimal("0")
        assert bucket.actual_amount == Decimal("0")

    assert await worker.run_once()

    async with sessions() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(str(upload["jobId"])))
            )
        ).scalar_one()
        calls = (
            await session.execute(select(GeminiCallRecord).order_by(GeminiCallRecord.id))
        ).scalars().all()
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()

    assert job.status == "completed"
    assert [call.state for call in calls] == ["failed", "succeeded"]
    assert [call.page_id for call in calls] == [None, job.page_id]
    assert [call.page_call_ordinal for call in calls] == [1, 1]
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == Decimal("0.52")
    await engine.dispose()
