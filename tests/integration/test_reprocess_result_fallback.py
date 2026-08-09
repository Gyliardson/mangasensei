from __future__ import annotations

import hashlib
import io
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.ocr.contracts import OcrEngine, OcrImage, OcrRegionResult, OcrResult
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class TextOcrFixture:
    def __init__(self, text: str) -> None:
        self._text = text

    async def analyze(self, image: OcrImage) -> OcrResult:
        dimensions = PageDimensions(width=80, height=120)
        bbox = BoundingBox(x=10, y=20, width=40, height=60)
        return OcrResult(
            image_sha256=image.sha256,
            regions=(
                OcrRegionResult(
                    id=REGION_ID,
                    dimensions=dimensions,
                    bbox=bbox,
                    normalized_bbox=bbox.normalize(dimensions),
                    polygon=((10, 20), (50, 20), (50, 80), (10, 80)),
                    angle=0.0,
                    confidence=0.97,
                    japanese_text=self._text,
                    reading_order=0,
                    detector="fixture",
                    recognizer="fixture",
                    upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
                ),
            ),
        )


class FailingOcrFixture:
    async def analyze(self, image: OcrImage) -> OcrResult:
        del image
        raise RuntimeError("deterministic reprocess failure")


class TokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        if text == "猫です":
            return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))
        if text == "犬です":
            return (("犬", "犬", "イヌ", "名詞"), ("です", "です", "デス", "助動詞"))
        raise AssertionError(f"unexpected fixture text: {text}")


class DictionaryFixture:
    version = "JMdict reprocess test"
    digest = hashlib.sha256(version.encode()).digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        del reading
        if lemma == "猫":
            return DictionaryEntry(
                id="jmdict-cat",
                meanings=("cat",),
                source=self.version,
                jlpt_level="N5",
                jlpt_official=False,
            )
        if lemma == "犬":
            return DictionaryEntry(
                id="jmdict-dog",
                meanings=("dog",),
                source=self.version,
                jlpt_level="N5",
                jlpt_official=False,
            )
        return None


def settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        capability_peppers=("reprocess-result-pepper-value-00000001",),
    )


def worker(
    sessions: async_sessionmaker[AsyncSession], root: Path, ocr: OcrEngine
) -> Worker:
    return Worker(
        sessions=sessions,
        storage=LocalFilesystemStorage(root),
        ocr=ocr,
        linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
        gemini=None,
        worker_id="reprocess-result-worker",
        lease_seconds=60,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reprocess_keeps_last_completed_result_until_replacement_completes(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "reprocess-fallback-upload-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]
        read_headers = {"X-Page-Token": upload_data["capabilities"]["readPage"]}
        reprocess_headers = {
            "X-Page-Token": upload_data["capabilities"]["reprocessPage"],
            "Idempotency-Key": "reprocess-fallback-job-0001",
        }

        engine, sessions = create_database(clean_postgres_url)
        try:
            assert await worker(sessions, tmp_path, TextOcrFixture("猫です")).run_once()

            first_result = (
                await client.get(f"/api/v1/pages/{upload_data['pageId']}", headers=read_headers)
            ).json()["data"]
            assert first_result["status"] == "completed"
            assert first_result["resultAvailable"] is True
            assert first_result["regions"][0]["text"] == "猫です"
            assert first_result["regions"][0]["vocabulary"][0]["meanings"] == ["cat"]

            pending_reprocess = await client.post(
                f"/api/v1/pages/{upload_data['pageId']}/reprocess",
                headers=reprocess_headers,
            )
            assert pending_reprocess.status_code == 202

            pending_status = (
                await client.get(
                    f"/api/v1/pages/{upload_data['pageId']}/status", headers=read_headers
                )
            ).json()["data"]
            pending_page = (
                await client.get(f"/api/v1/pages/{upload_data['pageId']}", headers=read_headers)
            ).json()["data"]
            assert pending_status == {
                "status": "pending",
                "error": None,
                "resultAvailable": True,
            }
            assert pending_page["status"] == "pending"
            assert pending_page["resultAvailable"] is True
            assert pending_page["regions"][0]["text"] == "猫です"

            async with sessions.begin() as session:
                page_id = (
                    await session.execute(
                        select(JobRecord.page_id).where(
                            JobRecord.public_id == UUID(upload_data["jobId"])
                        )
                    )
                ).scalar_one()
                latest_reprocess = (
                    await session.execute(
                        select(JobRecord)
                        .where(
                            JobRecord.page_id == page_id,
                            JobRecord.job_kind == "page_reprocess",
                        )
                        .order_by(JobRecord.id.desc())
                        .limit(1)
                    )
                ).scalar_one()
                latest_reprocess.max_attempts = 1

            assert await worker(sessions, tmp_path, FailingOcrFixture()).run_once()

            failed_status = (
                await client.get(
                    f"/api/v1/pages/{upload_data['pageId']}/status", headers=read_headers
                )
            ).json()["data"]
            failed_page = (
                await client.get(f"/api/v1/pages/{upload_data['pageId']}", headers=read_headers)
            ).json()["data"]
            assert failed_status["status"] == "failed"
            assert failed_status["error"]["code"] == "processing_failed"
            assert failed_status["resultAvailable"] is True
            assert failed_page["status"] == "failed"
            assert failed_page["error"]["code"] == "processing_failed"
            assert failed_page["regions"][0]["text"] == "猫です"

            replacement = await client.post(
                f"/api/v1/pages/{upload_data['pageId']}/reprocess",
                headers={**reprocess_headers, "Idempotency-Key": "reprocess-fallback-job-0002"},
            )
            assert replacement.status_code == 202
            assert await worker(sessions, tmp_path, TextOcrFixture("犬です")).run_once()

            replacement_page = (
                await client.get(f"/api/v1/pages/{upload_data['pageId']}", headers=read_headers)
            ).json()["data"]
            assert replacement_page["status"] == "completed"
            assert replacement_page["resultAvailable"] is True
            assert replacement_page["regions"][0]["text"] == "犬です"
            assert replacement_page["regions"][0]["vocabulary"][0]["meanings"] == ["dog"]
        finally:
            await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_page_without_completed_result_stays_empty_when_attempt_fails(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "reprocess-no-result-upload-0001"},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        upload_data = upload.json()["data"]
        read_headers = {"X-Page-Token": upload_data["capabilities"]["readPage"]}

        pending_page = (
            await client.get(f"/api/v1/pages/{upload_data['pageId']}", headers=read_headers)
        ).json()["data"]
        assert pending_page["status"] == "pending"
        assert pending_page["resultAvailable"] is False
        assert pending_page["regions"] == []

        engine, sessions = create_database(clean_postgres_url)
        try:
            async with sessions.begin() as session:
                await session.execute(
                    update(JobRecord)
                    .where(JobRecord.public_id == UUID(upload_data["jobId"]))
                    .values(max_attempts=1)
                )
            assert await worker(sessions, tmp_path, FailingOcrFixture()).run_once()

            failed_page = (
                await client.get(f"/api/v1/pages/{upload_data['pageId']}", headers=read_headers)
            ).json()["data"]
            failed_status = (
                await client.get(
                    f"/api/v1/pages/{upload_data['pageId']}/status", headers=read_headers
                )
            ).json()["data"]
            assert failed_page["status"] == "failed"
            assert failed_page["resultAvailable"] is False
            assert failed_page["regions"] == []
            assert failed_status["status"] == "failed"
            assert failed_status["resultAvailable"] is False
        finally:
            await engine.dispose()
