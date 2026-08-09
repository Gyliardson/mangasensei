from __future__ import annotations

import asyncio
import hashlib
import io
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select, text, update

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.infrastructure.database.analysis_models import (
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
    LinguisticRunRecord,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.queue_repository import QueueRepository
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.fake import DEFAULT_FAKE_PROVENANCE
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker


def fixture_image() -> bytes:
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
                    id="5ca22b32-6834-59db-a183-428a557a22e8",
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


class SlowOcrFixture(OcrFixture):
    async def analyze(self, image: OcrImage) -> OcrResult:
        await asyncio.sleep(1.25)
        return await super().analyze(image)


class TokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))


class DictionaryFixture:
    version = "JMdict test"
    digest = hashlib.sha256(b"JMdict test").digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        if lemma == "猫":
            return DictionaryEntry(
                id="jmdict-1467640",
                meanings=("gato",),
                source="JMdict test",
                jlpt_level="N5",
                jlpt_official=False,
            )
        return None


class GeminiFixture:
    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        assert "猫です" in prompt
        return schema(
            regions=(
                GeminiRegionAnalysis(
                    region_id="5ca22b32-6834-59db-a183-428a557a22e8",
                    translation="É um gato.",
                    explanation="Frase nominal polida.",
                    grammar_points=("です",),
                    vocabulary_ids=("jmdict-1467640",),
                ),
            )
        )


class FlakyGeminiFixture(GeminiFixture):
    def __init__(self) -> None:
        self.calls = 0

    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("simulated provider timeout")
        return await super().analyze(prompt=prompt, schema=schema)


def settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("worker-flow-pepper-value-00000001",),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_processes_uploaded_page_and_api_exposes_complete_result(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "worker-flow-integration-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=OcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=GeminiFixture(),
            worker_id="integration-worker",
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
        assert data["regions"][0]["text"] == "猫です"
        assert data["regions"][0]["translation"] == "É um gato."
        assert data["regions"][0]["vocabulary"][0]["meanings"] == ["gato"]
        assert data["regions"][0]["vocabulary"][0]["jlpt"] == {
            "level": "N5",
            "official": False,
        }
        async with sessions() as session:
            linguistic_run = (
                await session.execute(select(LinguisticRunRecord))
            ).scalar_one()
        assert linguistic_run.dictionary_version == DictionaryFixture.version
        assert linguistic_run.dictionary_digest == DictionaryFixture.digest
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_recovers_an_expired_lease_before_claiming(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "worker-recovery-integration-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        abandoned = await QueueRepository(sessions).claim(
            worker_id="abandoned-worker", lease_seconds=60
        )
        assert abandoned is not None
        async with sessions.begin() as session:
            await session.execute(
                update(JobRecord)
                .where(JobRecord.id == abandoned.job_id)
                .values(lease_expires_at=func.now() - text("interval '1 second'"))
            )

        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=OcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=GeminiFixture(),
            worker_id="recovery-worker",
            lease_seconds=60,
        )

        assert await worker.run_once()

        async with sessions() as session:
            job = (
                await session.execute(select(JobRecord).where(JobRecord.id == abandoned.job_id))
            ).scalar_one()
            attempts = (
                (
                    await session.execute(
                        select(JobAttemptRecord)
                        .where(JobAttemptRecord.job_id == abandoned.job_id)
                        .order_by(JobAttemptRecord.attempt_no)
                    )
                )
                .scalars()
                .all()
            )
        assert job.status == "completed"
        assert job.fencing_token == abandoned.fencing_token + 1
        assert [attempt.outcome for attempt in attempts] == ["lease_expired", "completed"]

        result = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert result.json()["data"]["status"] == "completed"
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_renews_lease_during_slow_ocr(clean_postgres_url: str, tmp_path: Path) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "worker-heartbeat-integration-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=SlowOcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=None,
            worker_id="heartbeat-worker",
            lease_seconds=1,
        )

        assert await worker.run_once()

        result = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert result.json()["data"]["status"] == "completed"
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_retries_after_an_uncertain_gemini_call_without_reservation_leak(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "worker-retry-integration-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        gemini = FlakyGeminiFixture()
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=OcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=gemini,
            worker_id="retry-worker",
            lease_seconds=60,
        )

        assert await worker.run_once()
        async with sessions.begin() as session:
            job = (
                await session.execute(
                    select(JobRecord).where(JobRecord.public_id == UUID(upload_data["jobId"]))
                )
            ).scalar_one()
            assert job.status == "retryable_failure"
            job.available_at = func.now()

        assert await worker.run_once()

        async with sessions() as session:
            job = (
                await session.execute(
                    select(JobRecord).where(JobRecord.public_id == UUID(upload_data["jobId"]))
                )
            ).scalar_one()
            attempts = (
                (
                    await session.execute(
                        select(JobAttemptRecord)
                        .where(JobAttemptRecord.job_id == job.id)
                        .order_by(JobAttemptRecord.attempt_no)
                    )
                )
                .scalars()
                .all()
            )
            calls = (
                (
                    await session.execute(
                        select(GeminiCallRecord).order_by(GeminiCallRecord.page_call_ordinal)
                    )
                )
                .scalars()
                .all()
            )
            bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()
        assert job.status == "completed"
        assert [attempt.outcome for attempt in attempts] == ["retryable_failure", "completed"]
        assert [call.state for call in calls] == ["unknown", "succeeded"]
        assert bucket.reserved_amount == Decimal("0")
        assert bucket.actual_amount == Decimal("1.04")
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reprocess_is_page_scoped_idempotent_and_runs_again(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = settings(clean_postgres_url, tmp_path)
    app = create_app(application_settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "reprocess-upload-integration-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]

        engine, sessions = create_database(clean_postgres_url)
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=OcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=None,
            worker_id="reprocess-worker",
            lease_seconds=60,
        )
        assert await worker.run_once()

        endpoint = f"/api/v1/pages/{upload_data['pageId']}/reprocess"
        headers = {
            "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
            "Idempotency-Key": "reprocess-job-integration-0001",
        }
        first = await client.post(endpoint, headers=headers)
        replay = await client.post(endpoint, headers=headers)
        concurrent = await client.post(
            endpoint,
            headers={**headers, "Idempotency-Key": "reprocess-job-integration-0002"},
        )
        denied = await client.post(
            endpoint,
            headers={**headers, "X-Page-Token": "invalid-token-value"},
        )

        assert first.status_code == 202
        assert replay.status_code == 200
        assert first.json()["data"]["jobId"] == replay.json()["data"]["jobId"]
        assert concurrent.status_code == 409
        assert concurrent.json()["error"]["code"] == "analysis_in_progress"
        assert denied.status_code == 404

        assert await worker.run_once()
        async with sessions() as session:
            jobs = (
                (
                    await session.execute(
                        select(JobRecord).order_by(JobRecord.created_at, JobRecord.id)
                    )
                )
                .scalars()
                .all()
            )
        assert [job.status for job in jobs] == ["completed", "completed"]
        assert [job.job_kind for job in jobs] == ["page_analysis", "page_reprocess"]
        await engine.dispose()
