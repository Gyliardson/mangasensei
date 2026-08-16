from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.infrastructure.database.analysis_models import (
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.queue_repository import QueueRepository
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.linguistics.service import LinguisticService
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import CancellationAcknowledgedError, Worker
from tests.integration.test_document_api import make_settings
from tests.integration.test_worker_flow import (
    DictionaryFixture,
    GeminiFixture,
    OcrFixture,
    TokenizerFixture,
    fixture_image,
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


async def _wait_for_lock_wait(
    sessions: async_sessionmaker[AsyncSession],
    *,
    query_fragments: tuple[str, ...],
) -> str:
    deadline = asyncio.get_running_loop().time() + 5.0
    while True:
        async with sessions() as session:
            queries = tuple(
                (
                    await session.execute(
                        text(
                            """
                            SELECT query
                            FROM pg_stat_activity
                            WHERE datname = current_database()
                              AND pid <> pg_backend_pid()
                              AND wait_event_type = 'Lock'
                            """
                        )
                    )
                ).scalars()
            )
        for query in queries:
            if any(fragment in query for fragment in query_fragments):
                return query
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(
                f"timed out waiting for PostgreSQL lock wait matching {query_fragments!r}"
            )
        await asyncio.sleep(0.01)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_cancel_and_gemini_reservation_share_page_then_job_order(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = QueueRepository(sessions)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "cancel-gemini-order-upload"},
            files=[("images[]", ("page.png", fixture_image(), "image/png"))],
        )
        assert upload.status_code == 202
        document = upload.json()["data"]

        claim = await queue.claim(worker_id="cancel-gemini-order-worker", lease_seconds=60)
        assert claim is not None
        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=OcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=GeminiFixture(),
            worker_id=claim.worker_id,
            lease_seconds=60,
        )
        await worker._transition(claim, "claimed", "processing_ocr")
        await worker._transition(claim, "processing_ocr", "processing_linguistics")
        await worker._transition(claim, "processing_linguistics", "processing_gemini")

        async with sessions() as session:
            page = await session.get_one(PageRecord, claim.page_id)

        today = datetime.now(UTC).date()
        async with sessions.begin() as session:
            session.add(
                GeminiBudgetBucketRecord(
                    budget_date=today,
                    currency="USD",
                    limit_amount=Decimal("100.00"),
                    reserved_amount=Decimal("0"),
                    actual_amount=Decimal("0"),
                )
            )

        blocker = sessions()
        await blocker.begin()
        await blocker.execute(
            select(GeminiBudgetBucketRecord)
            .where(
                GeminiBudgetBucketRecord.budget_date == today,
                GeminiBudgetBucketRecord.currency == "USD",
            )
            .with_for_update()
        )
        reserve_task = asyncio.create_task(worker._reserve_gemini_call(claim, page, "{}"))
        await _wait_for_lock_wait(
            sessions,
            query_fragments=("mangasensei.gemini_budget_buckets",),
        )
        cancel_task = asyncio.create_task(
            client.post(
                f"/api/v1/documents/{document['documentId']}/cancel",
                headers={"X-Document-Token": document["capabilities"]["manageDocument"]},
            )
        )
        try:
            blocked_cancel_query = await _wait_for_lock_wait(
                sessions,
                query_fragments=("mangasensei.pages", "mangasensei.jobs"),
            )
            await blocker.commit()
            call_id, cancel_response = await asyncio.wait_for(
                asyncio.gather(reserve_task, cancel_task),
                timeout=5.0,
            )
        finally:
            if blocker.in_transaction():
                await blocker.rollback()
            await blocker.close()
            for task in (reserve_task, cancel_task):
                if not task.done():
                    task.cancel()

        assert "mangasensei.pages" in blocked_cancel_query
        assert cancel_response.status_code == 200
        assert cancel_response.json()["data"]["cancelRequestedPages"] == 1
        assert cancel_response.json()["data"]["cancelledPages"] == 0

        with pytest.raises(CancellationAcknowledgedError):
            await worker._checkpoint_cancellation(claim, "processing_gemini")

        async with sessions() as session:
            job = await session.get_one(JobRecord, claim.job_id)
            call = await session.get_one(GeminiCallRecord, call_id)
            bucket = (
                await session.execute(
                    select(GeminiBudgetBucketRecord).where(
                        GeminiBudgetBucketRecord.budget_date == today,
                        GeminiBudgetBucketRecord.currency == "USD",
                    )
                )
            ).scalar_one()
        assert job.status == "cancelled"
        assert job.fencing_token == claim.fencing_token
        assert call.state == "failed"
        assert call.page_id is None
        assert bucket.reserved_amount == Decimal("0")

    await engine.dispose()
