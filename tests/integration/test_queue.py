from __future__ import annotations

import asyncio
import hashlib

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.queue_repository import QueueRepository
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord


def async_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


async def seed_job(
    sessions: async_sessionmaker[AsyncSession], *, seed: bytes, max_attempts: int = 3
) -> int:
    digest = hashlib.sha256(seed).digest()
    async with sessions.begin() as session:
        blob = ImageBlobRecord(
            sha256=digest,
            byte_size=100,
            width=10,
            height=10,
            media_type="image/png",
            storage_key=f"objects/{digest.hex()[:2]}/{digest.hex()[2:4]}/{digest.hex()}",
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
            max_attempts=max_attempts,
        )
        session.add(job)
        await session.flush()
        return job.id


async def expire_lease(sessions: async_sessionmaker[AsyncSession], job_id: int) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id)
            .values(lease_expires_at=func.now() - text("interval '1 second'"))
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_workers_cannot_claim_the_same_job(clean_postgres_url: str) -> None:
    engine = create_async_engine(async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await seed_job(sessions, seed=b"queue-fixture")

    first, second = await asyncio.gather(
        QueueRepository(sessions).claim(worker_id="worker-a", lease_seconds=60),
        QueueRepository(sessions).claim(worker_id="worker-b", lease_seconds=60),
    )

    claims = [claim for claim in (first, second) if claim is not None]
    assert len(claims) == 1
    claim = claims[0]
    assert claim.attempt_no == 1
    assert claim.fencing_token == 1
    assert not await QueueRepository(sessions).heartbeat(
        job_id=claim.job_id,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token + 1,
        lease_seconds=60,
    )
    assert await QueueRepository(sessions).heartbeat(
        job_id=claim.job_id,
        worker_id=claim.worker_id,
        fencing_token=claim.fencing_token,
        lease_seconds=60,
    )
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_lease_is_closed_and_can_be_reclaimed(clean_postgres_url: str) -> None:
    engine = create_async_engine(async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    job_id = await seed_job(sessions, seed=b"recoverable-job")
    repository = QueueRepository(sessions)
    first = await repository.claim(worker_id="worker-a", lease_seconds=60)
    assert first is not None
    await expire_lease(sessions, job_id)

    assert await repository.recover_expired_leases() == 1

    async with sessions() as session:
        job = await session.get_one(JobRecord, job_id)
        attempt = (
            await session.execute(
                select(JobAttemptRecord).where(
                    JobAttemptRecord.job_id == job_id,
                    JobAttemptRecord.fencing_token == first.fencing_token,
                )
            )
        ).scalar_one()
    assert job.status == "retryable_failure"
    assert job.worker_id is None
    assert job.error_code == "lease_expired"
    assert attempt.outcome == "lease_expired"
    assert attempt.ended_at is not None

    second = await repository.claim(worker_id="worker-b", lease_seconds=60)
    assert second is not None
    assert second.job_id == job_id
    assert second.attempt_no == 2
    assert second.fencing_token == first.fencing_token + 1
    assert not await repository.heartbeat(
        job_id=job_id,
        worker_id="worker-a",
        fencing_token=first.fencing_token,
        lease_seconds=60,
    )
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_cancel_requested_lease_terminalizes_instead_of_requeueing(
    clean_postgres_url: str,
) -> None:
    engine = create_async_engine(async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    job_id = await seed_job(sessions, seed=b"cancelled-recovery-job")
    repository = QueueRepository(sessions)
    claim = await repository.claim(worker_id="worker-a", lease_seconds=60)
    assert claim is not None

    async with sessions.begin() as session:
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id)
            .values(
                cancel_requested_at=func.now(),
                lease_expires_at=func.now() - text("interval '1 second'"),
            )
        )

    assert await repository.recover_expired_leases() == 1

    async with sessions() as session:
        job = await session.get_one(JobRecord, job_id)
        attempt = (
            await session.execute(
                select(JobAttemptRecord).where(
                    JobAttemptRecord.job_id == job_id,
                    JobAttemptRecord.fencing_token == claim.fencing_token,
                )
            )
        ).scalar_one()
    assert job.status == "cancelled"
    assert job.finished_at is not None
    assert job.worker_id is None
    assert job.heartbeat_at is None
    assert job.lease_expires_at is None
    assert job.error_code is None
    assert job.error_detail is None
    assert attempt.outcome == "cancelled"
    assert attempt.error_code is None
    assert attempt.error_detail is None
    assert await repository.claim(worker_id="worker-b", lease_seconds=60) is None
    assert not await repository.heartbeat(
        job_id=job_id,
        worker_id="worker-a",
        fencing_token=claim.fencing_token,
        lease_seconds=60,
    )
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_lease_with_no_attempts_left_fails(clean_postgres_url: str) -> None:
    engine = create_async_engine(async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    job_id = await seed_job(sessions, seed=b"exhausted-job", max_attempts=1)
    repository = QueueRepository(sessions)
    assert await repository.claim(worker_id="worker-a", lease_seconds=60) is not None
    await expire_lease(sessions, job_id)

    assert await repository.recover_expired_leases() == 1

    async with sessions() as session:
        job = await session.get_one(JobRecord, job_id)
    assert job.status == "failed"
    assert job.finished_at is not None
    assert await repository.claim(worker_id="worker-b", lease_seconds=60) is None
    await engine.dispose()
