from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.queue_repository import QueueRepository
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.linguistics.service import LinguisticService
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import CancellationAcknowledgedError, StaleLeaseError, Worker
from tests.integration.job_fixture_helpers import advance_pending_job_to_processing_linguistics
from tests.integration.test_document_api import _add_completed_result, make_settings
from tests.integration.test_worker_flow import (
    DictionaryFixture,
    OcrFixture,
    TokenizerFixture,
    fixture_image,
)


def _async_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


async def _upload_document(
    client: AsyncClient,
    *,
    count: int,
    key: str,
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/documents",
        headers={"Idempotency-Key": key},
        files=[
            ("images[]", (f"page-{index}.png", fixture_image(), "image/png"))
            for index in range(count)
        ],
    )
    assert response.status_code == 202
    return response.json()["data"]


async def _page_and_latest_job(
    session: AsyncSession,
    page_public_id: str,
) -> tuple[PageRecord, JobRecord]:
    page = (
        await session.execute(
            select(PageRecord).where(PageRecord.public_id == UUID(page_public_id))
        )
    ).scalar_one()
    job = (
        await session.execute(
            select(JobRecord)
            .where(JobRecord.page_id == page.id)
            .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
            .limit(1)
        )
    ).scalar_one()
    return page, job


async def _complete_page(
    sessions: async_sessionmaker[AsyncSession],
    page_public_id: str,
    *,
    seed: bytes,
) -> None:
    async with sessions.begin() as session:
        _, job = await _page_and_latest_job(session, page_public_id)
        await advance_pending_job_to_processing_linguistics(session, job)
        await _add_completed_result(session, job=job, digest=hashlib.sha256(seed).digest())
        job.status = "completed"
        job.finished_at = datetime.now(UTC)
        job.worker_id = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        await session.flush()


async def _fail_page(
    sessions: async_sessionmaker[AsyncSession],
    page_public_id: str,
) -> None:
    async with sessions.begin() as session:
        _, job = await _page_and_latest_job(session, page_public_id)
        await advance_pending_job_to_processing_linguistics(session, job)
        job.status = "failed"
        job.finished_at = datetime.now(UTC)
        job.worker_id = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.error_code = "processing_failed"
        job.error_detail = "fixture terminal failure"
        await session.flush()


async def _make_retryable(
    sessions: async_sessionmaker[AsyncSession],
    page_public_id: str,
) -> None:
    async with sessions.begin() as session:
        _, job = await _page_and_latest_job(session, page_public_id)
        await advance_pending_job_to_processing_linguistics(session, job)
        job.status = "retryable_failure"
        job.available_at = func.now() + text("interval '1 hour'")
        job.worker_id = None
        job.heartbeat_at = None
        job.lease_expires_at = None
        job.error_code = "provider_timeout"
        job.error_detail = "fixture retryable failure"
        await session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_completed_with_errors_retry_is_bounded_exact_and_idempotent(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = await _upload_document(client, count=2, key="slice-c-retry-upload")
        pages = document["pages"]
        assert isinstance(pages, list)
        completed_id = pages[0]["pageId"]
        failed_id = pages[1]["pageId"]
        await _complete_page(sessions, completed_id, seed=b"slice-c-completed")
        await _fail_page(sessions, failed_id)

        read = await client.get(
            f"/api/v1/documents/{document['documentId']}",
            headers={"X-Document-Token": document["capabilities"]["readDocument"]},
        )
        assert read.status_code == 200
        assert read.json()["data"]["status"] == "completed_with_errors"
        assert read.json()["data"]["progress"] == {
            "totalPages": 2,
            "completedPages": 1,
            "processingPages": 0,
            "failedPages": 1,
            "cancelledPages": 0,
        }

        endpoint = f"/api/v1/documents/{document['documentId']}/retry-failed"
        headers = {
            "X-Document-Token": document["capabilities"]["manageDocument"],
            "Idempotency-Key": "slice-c-retry-batch",
        }
        first = await client.post(endpoint, headers=headers)
        replay = await client.post(endpoint, headers=headers)
        while_active = await client.post(
            endpoint,
            headers={**headers, "Idempotency-Key": "slice-c-retry-batch-second"},
        )

        assert first.status_code == 202
        assert first.json()["data"]["created"] is True
        assert first.json()["data"]["retriedPageIds"] == [failed_id]
        assert replay.status_code == 200
        assert replay.json()["data"]["created"] is False
        assert replay.json()["data"]["retriedPageIds"] == [failed_id]
        assert replay.json()["data"]["jobIds"] == first.json()["data"]["jobIds"]
        assert while_active.status_code == 200
        assert while_active.json()["data"]["created"] is False
        assert while_active.json()["data"]["retriedPageIds"] == []

    async with sessions() as session:
        completed_page = (
            await session.execute(
                select(PageRecord).where(PageRecord.public_id == UUID(completed_id))
            )
        ).scalar_one()
        failed_page = (
            await session.execute(select(PageRecord).where(PageRecord.public_id == UUID(failed_id)))
        ).scalar_one()
        completed_jobs = tuple(
            (
                await session.execute(
                    select(JobRecord).where(JobRecord.page_id == completed_page.id)
                )
            ).scalars()
        )
        failed_jobs = tuple(
            (
                await session.execute(
                    select(JobRecord)
                    .where(JobRecord.page_id == failed_page.id)
                    .order_by(JobRecord.id)
                )
            ).scalars()
        )
    assert len(completed_jobs) == 1
    assert len(failed_jobs) == 2
    assert failed_jobs[-1].job_kind == "document_retry"
    assert failed_jobs[-1].status == "pending"
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_failed_never_duplicates_queue_owned_retryable_failure(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = await _upload_document(client, count=1, key="slice-c-retryable-upload")
        page_id = document["pages"][0]["pageId"]
        await _make_retryable(sessions, page_id)
        response = await client.post(
            f"/api/v1/documents/{document['documentId']}/retry-failed",
            headers={
                "X-Document-Token": document["capabilities"]["manageDocument"],
                "Idempotency-Key": "slice-c-retryable-batch",
            },
        )
    assert response.status_code == 200
    assert response.json()["data"]["created"] is False
    assert response.json()["data"]["retriedPageIds"] == []
    async with sessions() as session:
        page, _ = await _page_and_latest_job(session, page_id)
        count = await session.scalar(
            select(func.count()).select_from(JobRecord).where(JobRecord.page_id == page.id)
        )
    assert count == 1
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_cancel_preserves_completed_page_with_owner_and_recovery(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    queue = QueueRepository(sessions)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = await _upload_document(client, count=5, key="slice-c-cancel-upload")
        page_ids = [page["pageId"] for page in document["pages"]]
        await _complete_page(sessions, page_ids[0], seed=b"slice-c-cancel-completed")

        claimed = await queue.claim(worker_id="cancel-worker-claimed", lease_seconds=60)
        processing = await queue.claim(worker_id="cancel-worker-processing", lease_seconds=60)
        assert claimed is not None
        assert processing is not None

        worker = Worker(
            sessions=sessions,
            storage=LocalFilesystemStorage(tmp_path),
            ocr=OcrFixture(),
            linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
            gemini=None,
            worker_id="cancel-checkpoint-worker",
            lease_seconds=60,
        )
        await worker._transition(processing, "claimed", "processing_ocr")
        await _make_retryable(sessions, page_ids[4])

        response = await client.post(
            f"/api/v1/documents/{document['documentId']}/cancel",
            headers={"X-Document-Token": document["capabilities"]["manageDocument"]},
        )
        assert response.status_code == 200
        assert response.json()["data"]["cancelRequestedPages"] == 2
        assert response.json()["data"]["cancelledPages"] == 2
        assert response.json()["data"]["status"] == "processing"

        with pytest.raises(StaleLeaseError):
            await worker._transition(claimed, "claimed", "processing_ocr")
        with pytest.raises(CancellationAcknowledgedError):
            await worker._checkpoint_cancellation(claimed, "claimed")

        async with sessions.begin() as session:
            active = await session.get_one(JobRecord, processing.job_id)
            active.lease_expires_at = func.now() - text("interval '1 second'")
        assert await queue.recover_expired_leases() == 1

        replay = await client.post(
            f"/api/v1/documents/{document['documentId']}/cancel",
            headers={"X-Document-Token": document["capabilities"]["manageDocument"]},
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["cancelRequestedPages"] == 0
        assert replay.json()["data"]["cancelledPages"] == 0
        assert replay.json()["data"]["status"] == "cancelled"

        after = await client.get(
            f"/api/v1/documents/{document['documentId']}",
            headers={"X-Document-Token": document["capabilities"]["readDocument"]},
        )
        assert after.json()["data"]["status"] == "cancelled"
        assert after.json()["data"]["progress"] == {
            "totalPages": 5,
            "completedPages": 1,
            "processingPages": 0,
            "failedPages": 0,
            "cancelledPages": 4,
        }
        completed_page = await client.get(
            f"/api/v1/documents/{document['documentId']}/pages/{page_ids[0]}",
            headers={"X-Document-Token": document["capabilities"]["readDocument"]},
        )
        assert completed_page.status_code == 200
        assert completed_page.json()["data"]["resultAvailable"] is True

    async with sessions() as session:
        terminal_jobs = tuple(
            (
                await session.execute(
                    select(JobRecord)
                    .join(PageRecord, PageRecord.id == JobRecord.page_id)
                    .where(PageRecord.document_id.is_not(None))
                    .order_by(PageRecord.ordinal, JobRecord.id)
                )
            ).scalars()
        )
    assert terminal_jobs[0].status == "completed"
    assert all(job.status == "cancelled" for job in terminal_jobs[1:])
    assert all(job.error_detail is None for job in terminal_jobs[1:])
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reorder_is_atomic_membership_exact_and_revisioned(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = await _upload_document(client, count=3, key="slice-c-reorder-upload")
        original = [page["pageId"] for page in document["pages"]]
        desired = tuple(reversed(original))
        async with sessions() as session:
            rows = tuple(
                (
                    await session.execute(
                        select(
                            PageRecord.id,
                            PageRecord.public_id,
                            PageRecord.expires_at,
                        )
                        .where(PageRecord.public_id.in_([UUID(value) for value in original]))
                        .order_by(PageRecord.id)
                    )
                ).all()
            )
            job_count_before = await session.scalar(select(func.count()).select_from(JobRecord))

        endpoint = f"/api/v1/documents/{document['documentId']}/order"
        headers = {"X-Document-Token": document["capabilities"]["manageDocument"]}
        reordered = await client.put(
            endpoint,
            headers=headers,
            json={"pageIds": desired, "expectedOrderRevision": 1},
        )
        stale = await client.put(
            endpoint,
            headers=headers,
            json={"pageIds": original, "expectedOrderRevision": 1},
        )
        duplicate = await client.put(
            endpoint,
            headers=headers,
            json={
                "pageIds": [original[0], original[0], original[2]],
                "expectedOrderRevision": 2,
            },
        )
        denied = await client.put(
            endpoint,
            headers={"X-Document-Token": document["capabilities"]["readDocument"]},
            json={"pageIds": original, "expectedOrderRevision": 2},
        )
        reloaded = await client.get(
            f"/api/v1/documents/{document['documentId']}",
            headers={"X-Document-Token": document["capabilities"]["readDocument"]},
        )

    assert reordered.status_code == 200
    assert reordered.json()["data"]["orderRevision"] == 2
    assert [page["pageId"] for page in reordered.json()["data"]["pages"]] == list(desired)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "order_revision_conflict"
    assert duplicate.status_code == 422
    assert duplicate.json()["error"]["code"] == "invalid_document_order"
    assert denied.status_code == 404
    assert [page["pageId"] for page in reloaded.json()["data"]["pages"]] == list(desired)

    async with sessions() as session:
        after_rows = tuple(
            (
                await session.execute(
                    select(
                        PageRecord.id,
                        PageRecord.public_id,
                        PageRecord.ordinal,
                        PageRecord.expires_at,
                    )
                    .where(PageRecord.public_id.in_([UUID(value) for value in original]))
                    .order_by(PageRecord.id)
                )
            ).all()
        )
        job_count_after = await session.scalar(select(func.count()).select_from(JobRecord))
    assert [row.id for row in after_rows] == [row.id for row in rows]
    assert [row.expires_at for row in after_rows] == [row.expires_at for row in rows]
    assert sorted(row.ordinal for row in after_rows) == [0, 1, 2]
    assert job_count_after == job_count_before
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_reprocess_race_preserves_single_active_job(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = await _upload_document(client, count=1, key="slice-c-race-upload")
        page_id = document["pages"][0]["pageId"]
        await _fail_page(sessions, page_id)
        retry, reprocess = await asyncio.gather(
            client.post(
                f"/api/v1/documents/{document['documentId']}/retry-failed",
                headers={
                    "X-Document-Token": document["capabilities"]["manageDocument"],
                    "Idempotency-Key": "slice-c-race-retry",
                },
            ),
            client.post(
                f"/api/v1/documents/{document['documentId']}/pages/{page_id}/reprocess",
                headers={
                    "X-Document-Token": document["capabilities"]["reprocessDocument"],
                    "Idempotency-Key": "slice-c-race-reprocess",
                },
                json={"studyLanguage": "pt-BR"},
            ),
        )

    assert retry.status_code in {200, 202}
    assert reprocess.status_code in {202, 409}
    assert retry.status_code == 202 or reprocess.status_code == 202
    async with sessions() as session:
        page, _ = await _page_and_latest_job(session, page_id)
        active = await session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.page_id == page.id,
                JobRecord.status.in_(
                    (
                        "pending",
                        "claimed",
                        "processing_ocr",
                        "processing_linguistics",
                        "processing_gemini",
                        "retryable_failure",
                    )
                ),
            )
        )
    assert active == 1
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retry_cancel_race_never_creates_duplicate_active_work(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = await _upload_document(client, count=1, key="slice-c-retry-cancel-race-upload")
        page_id = document["pages"][0]["pageId"]
        await _fail_page(sessions, page_id)
        retry, cancel = await asyncio.gather(
            client.post(
                f"/api/v1/documents/{document['documentId']}/retry-failed",
                headers={
                    "X-Document-Token": document["capabilities"]["manageDocument"],
                    "Idempotency-Key": "slice-c-retry-cancel-race",
                },
            ),
            client.post(
                f"/api/v1/documents/{document['documentId']}/cancel",
                headers={"X-Document-Token": document["capabilities"]["manageDocument"]},
            ),
        )

    assert retry.status_code == 202
    assert cancel.status_code == 200
    async with sessions() as session:
        page, latest = await _page_and_latest_job(session, page_id)
        active = await session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.page_id == page.id,
                JobRecord.status.in_(
                    (
                        "pending",
                        "claimed",
                        "processing_ocr",
                        "processing_linguistics",
                        "processing_gemini",
                        "retryable_failure",
                    )
                ),
            )
        )
    assert active in {0, 1}
    assert latest.status in {"pending", "cancelled"}
    await engine.dispose()
