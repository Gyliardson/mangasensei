from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mangasensei.infrastructure.database.analysis_models import (
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
    GeminiCostLedgerRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.queue_repository import ClaimedJob, QueueRepository
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.retention import RetentionJanitor

_RESERVATION = Decimal("0.52")


def _async_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _validated_image(seed: bytes) -> ValidatedImage:
    return ValidatedImage(
        content=seed,
        sha256=hashlib.sha256(seed).hexdigest(),
        width=10,
        height=10,
        media_type="image/png",
        format="PNG",
    )


async def _seed_page_job(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: bytes,
    storage: LocalFilesystemStorage | None = None,
) -> tuple[int, int]:
    image = _validated_image(seed)
    storage_key = (
        await storage.store(image)
        if storage is not None
        else f"objects/{image.sha256[:2]}/{image.sha256[2:4]}/{image.sha256}"
    )
    digest = bytes.fromhex(image.sha256)
    async with sessions.begin() as session:
        blob = ImageBlobRecord(
            sha256=digest,
            byte_size=len(image.content),
            width=image.width,
            height=image.height,
            media_type=image.media_type,
            storage_key=storage_key,
        )
        session.add(blob)
        await session.flush()
        page = PageRecord(
            image_blob_id=blob.id,
            original_filename="fixture.png",
            upload_key_id="v1",
            upload_idempotency_digest=hashlib.sha256(seed + b"-upload").digest(),
            request_digest=digest,
        )
        session.add(page)
        await session.flush()
        job = JobRecord(
            page_id=page.id,
            idempotency_digest=hashlib.sha256(seed + b"-job").digest(),
            request_digest=digest,
        )
        session.add(job)
        await session.flush()
        return page.id, job.id


async def _advance_claim_to_gemini(
    sessions: async_sessionmaker[AsyncSession], claim: ClaimedJob
) -> None:
    for status in ("processing_ocr", "processing_linguistics", "processing_gemini"):
        async with sessions.begin() as session:
            await session.execute(
                update(JobRecord)
                .where(JobRecord.id == claim.job_id)
                .values(status=status, updated_at=func.now())
            )


async def _claim_for_gemini(
    sessions: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
) -> ClaimedJob:
    claim = await QueueRepository(sessions).claim(worker_id=worker_id, lease_seconds=60)
    assert claim is not None
    await _advance_claim_to_gemini(sessions, claim)
    return claim


async def _seed_open_call(
    sessions: async_sessionmaker[AsyncSession],
    *,
    page_id: int,
    claim: ClaimedJob,
    state: str,
) -> int:
    today = datetime.now(UTC).date()
    async with sessions.begin() as session:
        session.add(
            GeminiBudgetBucketRecord(
                budget_date=today,
                currency="USD",
                limit_amount=Decimal("5.00"),
                reserved_amount=_RESERVATION,
                actual_amount=Decimal("0"),
            )
        )
        call = GeminiCallRecord(
            page_id=page_id,
            job_id=claim.job_id,
            page_call_ordinal=1,
            fencing_token=claim.fencing_token,
            model="fixture-model",
            prompt_version="page-study-v1",
            schema_version="v1",
            request_digest=hashlib.sha256(b"gemini-request").digest(),
            reserved_cost=_RESERVATION,
            state=state,
            sent_at=datetime.now(UTC) if state == "sent" else None,
        )
        session.add(call)
        await session.flush()
        return call.id


async def _expire_claim(
    sessions: async_sessionmaker[AsyncSession],
    *,
    job_id: int,
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id)
            .values(lease_expires_at=func.now() - text("interval '1 second'"))
        )


async def _accounting_snapshot(
    sessions: async_sessionmaker[AsyncSession],
    call_id: int,
) -> tuple[GeminiCallRecord, GeminiBudgetBucketRecord, list[GeminiCostLedgerRecord]]:
    async with sessions() as session:
        call = await session.get_one(GeminiCallRecord, call_id)
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()
        ledger = (
            await session.execute(
                select(GeminiCostLedgerRecord).where(
                    GeminiCostLedgerRecord.gemini_call_id == call_id
                )
            )
        ).scalars().all()
        return call, bucket, ledger


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_state", "expected_actual", "expected_ledger"),
    [
        ("reserved", "failed", Decimal("0"), 0),
        ("sent", "unknown", _RESERVATION, 1),
    ],
)
async def test_expired_lease_reconciles_open_gemini_calls_idempotently(
    clean_postgres_url: str,
    state: str,
    expected_state: str,
    expected_actual: Decimal,
    expected_ledger: int,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    page_id, _ = await _seed_page_job(sessions, seed=f"lease-{state}".encode())
    claim = await _claim_for_gemini(sessions, worker_id="worker-a")
    call_id = await _seed_open_call(sessions, page_id=page_id, claim=claim, state=state)
    await _expire_claim(sessions, job_id=claim.job_id)
    repository = QueueRepository(sessions)

    assert await repository.recover_expired_leases() == 1
    assert await repository.recover_expired_leases() == 0

    call, bucket, ledger = await _accounting_snapshot(sessions, call_id)
    assert call.state == expected_state
    assert call.finished_at is not None
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == expected_actual
    assert len(ledger) == expected_ledger
    if state == "reserved":
        assert call.page_id is None
    else:
        assert call.page_id == page_id
        assert ledger[0].usage_category == "unknown_upper_bound"
        assert ledger[0].amount == _RESERVATION

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reserved_lease_loss_does_not_consume_later_page_call_ordinal(
    clean_postgres_url: str,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    page_id, job_id = await _seed_page_job(sessions, seed=b"retry-after-reserved-loss")
    first = await _claim_for_gemini(sessions, worker_id="worker-a")
    abandoned_call_id = await _seed_open_call(
        sessions,
        page_id=page_id,
        claim=first,
        state="reserved",
    )
    await _expire_claim(sessions, job_id=job_id)

    assert await QueueRepository(sessions).recover_expired_leases() == 1
    second = await QueueRepository(sessions).claim(worker_id="worker-b", lease_seconds=60)
    assert second is not None
    assert second.fencing_token == first.fencing_token + 1
    await _advance_claim_to_gemini(sessions, second)

    async with sessions.begin() as session:
        bucket = (
            await session.execute(select(GeminiBudgetBucketRecord).with_for_update())
        ).scalar_one()
        bucket.reserved_amount += _RESERVATION
        replacement = GeminiCallRecord(
            page_id=page_id,
            job_id=job_id,
            page_call_ordinal=1,
            fencing_token=second.fencing_token,
            model="fixture-model",
            prompt_version="page-study-v1",
            schema_version="v1",
            request_digest=hashlib.sha256(b"replacement-request").digest(),
            reserved_cost=_RESERVATION,
        )
        session.add(replacement)
        await session.flush()
        replacement_call_id = replacement.id

    async with sessions() as session:
        abandoned = await session.get_one(GeminiCallRecord, abandoned_call_id)
        replacement = await session.get_one(GeminiCallRecord, replacement_call_id)
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()
    assert abandoned.state == "failed"
    assert abandoned.page_id is None
    assert replacement.page_id == page_id
    assert replacement.page_call_ordinal == 1
    assert bucket.reserved_amount == _RESERVATION
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_lease_recovery_charges_uncertain_call_once(
    clean_postgres_url: str,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    page_id, job_id = await _seed_page_job(sessions, seed=b"concurrent-sent-loss")
    claim = await _claim_for_gemini(sessions, worker_id="worker-a")
    call_id = await _seed_open_call(sessions, page_id=page_id, claim=claim, state="sent")
    await _expire_claim(sessions, job_id=job_id)

    recovered = await asyncio.gather(
        QueueRepository(sessions).recover_expired_leases(),
        QueueRepository(sessions).recover_expired_leases(),
    )

    assert sorted(recovered) == [0, 1]
    call, bucket, ledger = await _accounting_snapshot(sessions, call_id)
    assert call.state == "unknown"
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == _RESERVATION
    assert len(ledger) == 1
    assert ledger[0].amount == _RESERVATION
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "expected_state", "expected_actual", "expected_ledger"),
    [
        ("reserved", "failed", Decimal("0"), 0),
        ("sent", "unknown", _RESERVATION, 1),
    ],
)
async def test_retention_reconciles_open_gemini_call_before_page_deletion(
    clean_postgres_url: str,
    tmp_path: Path,
    state: str,
    expected_state: str,
    expected_actual: Decimal,
    expected_ledger: int,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    page_id, job_id = await _seed_page_job(
        sessions,
        seed=f"retention-{state}".encode(),
        storage=storage,
    )
    claim = await _claim_for_gemini(sessions, worker_id="worker-a")
    call_id = await _seed_open_call(sessions, page_id=page_id, claim=claim, state=state)
    expired_created_at = datetime.now(UTC) - timedelta(hours=25)
    async with sessions.begin() as session:
        await session.execute(
            update(PageRecord)
            .where(PageRecord.id == page_id)
            .values(
                created_at=expired_created_at,
                expires_at=expired_created_at + timedelta(hours=24),
            )
        )

    janitor = RetentionJanitor(sessions, storage)
    assert await janitor.run_once(batch_size=10) == 1
    assert await janitor.run_once(batch_size=10) == 0

    call, bucket, ledger = await _accounting_snapshot(sessions, call_id)
    assert call.state == expected_state
    assert call.page_id is None
    assert call.job_id is None
    assert call.finished_at is not None
    assert bucket.reserved_amount == Decimal("0")
    assert bucket.actual_amount == expected_actual
    assert len(ledger) == expected_ledger
    if ledger:
        assert ledger[0].usage_category == "unknown_upper_bound"
        assert ledger[0].amount == _RESERVATION
    async with sessions() as session:
        assert await session.get(PageRecord, page_id) is None
        assert await session.get(JobRecord, job_id) is None
    await engine.dispose()
