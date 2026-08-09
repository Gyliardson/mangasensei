"""Short transactional operations for the PostgreSQL worker queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.infrastructure.database.gemini_accounting import (
    reconcile_abandoned_gemini_calls,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.queue import build_claim_statement
from mangasensei.infrastructure.database.storage_models import PageRecord


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    job_id: int
    page_id: int
    worker_id: str
    attempt_no: int
    fencing_token: int
    lease_expires_at: datetime


_ACTIVE_STATUSES = (
    "claimed",
    "processing_ocr",
    "processing_linguistics",
    "processing_gemini",
)


class QueueRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def claim(self, *, worker_id: str, lease_seconds: int) -> ClaimedJob | None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                build_claim_statement(worker_id=worker_id, lease_seconds=lease_seconds)
            )
            job = result.scalar_one_or_none()
            if job is None:
                return None
            if job.lease_expires_at is None or job.heartbeat_at is None or job.claimed_at is None:
                raise RuntimeError("claimed job is missing lease timestamps")
            session.add(
                JobAttemptRecord(
                    job_id=job.id,
                    attempt_no=job.attempt_count,
                    fencing_token=job.fencing_token,
                    worker_id=worker_id,
                    claimed_at=job.claimed_at,
                    heartbeat_at=job.heartbeat_at,
                    lease_expires_at=job.lease_expires_at,
                )
            )
            return ClaimedJob(
                job_id=job.id,
                page_id=job.page_id,
                worker_id=worker_id,
                attempt_no=job.attempt_count,
                fencing_token=job.fencing_token,
                lease_expires_at=job.lease_expires_at,
            )

    async def recover_expired_leases(self, *, batch_size: int = 100) -> int:
        if not 1 <= batch_size <= 1_000:
            raise ValueError("batch_size must be between 1 and 1000")
        async with self._sessions.begin() as session:
            expired = (
                await session.execute(
                    select(
                        JobRecord.id,
                        JobRecord.fencing_token,
                        JobRecord.attempt_count,
                        JobRecord.max_attempts,
                        (PageRecord.expires_at <= func.now()).label("page_expired"),
                    )
                    .join(PageRecord, PageRecord.id == JobRecord.page_id)
                    .where(
                        JobRecord.status.in_(_ACTIVE_STATUSES),
                        JobRecord.lease_expires_at <= func.now(),
                    )
                    .order_by(JobRecord.lease_expires_at, JobRecord.id)
                    .limit(batch_size)
                    .with_for_update(of=JobRecord, skip_locked=True)
                )
            ).all()
            recovered = 0
            for job_id, fencing_token, attempt_count, max_attempts, page_expired in expired:
                target = (
                    "expired"
                    if page_expired
                    else "failed"
                    if attempt_count >= max_attempts
                    else "retryable_failure"
                )
                result = await session.execute(
                    update(JobRecord)
                    .where(
                        JobRecord.id == job_id,
                        JobRecord.fencing_token == fencing_token,
                        JobRecord.status.in_(_ACTIVE_STATUSES),
                        JobRecord.lease_expires_at <= func.now(),
                    )
                    .values(
                        status=target,
                        available_at=func.now(),
                        worker_id=None,
                        heartbeat_at=None,
                        lease_expires_at=None,
                        finished_at=func.now() if target in {"failed", "expired"} else None,
                        error_code="lease_expired",
                        error_detail="worker lease expired before completion",
                        updated_at=func.now(),
                    )
                    .returning(JobRecord.id)
                )
                if result.scalar_one_or_none() is None:
                    continue
                await reconcile_abandoned_gemini_calls(
                    session,
                    job_id=job_id,
                    fencing_token=fencing_token,
                )
                await session.execute(
                    update(JobAttemptRecord)
                    .where(
                        JobAttemptRecord.job_id == job_id,
                        JobAttemptRecord.fencing_token == fencing_token,
                        JobAttemptRecord.ended_at.is_(None),
                    )
                    .values(
                        ended_at=func.now(),
                        outcome="lease_expired",
                        error_code="lease_expired",
                        error_detail="worker lease expired before completion",
                    )
                )
                recovered += 1
            return recovered

    async def heartbeat(
        self,
        *,
        job_id: int,
        worker_id: str,
        fencing_token: int,
        lease_seconds: int,
    ) -> bool:
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        lease_interval = func.make_interval(0, 0, 0, 0, 0, 0, lease_seconds)
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(JobRecord)
                .where(
                    JobRecord.id == job_id,
                    JobRecord.worker_id == worker_id,
                    JobRecord.fencing_token == fencing_token,
                    JobRecord.status.in_(_ACTIVE_STATUSES),
                    JobRecord.lease_expires_at > func.now(),
                )
                .values(
                    heartbeat_at=func.now(),
                    lease_expires_at=func.now() + lease_interval,
                    updated_at=func.now(),
                )
                .returning(
                    JobRecord.attempt_count, JobRecord.heartbeat_at, JobRecord.lease_expires_at
                )
            )
            heartbeat = result.one_or_none()
            if heartbeat is None:
                return False
            attempt_no, heartbeat_at, lease_expires_at = heartbeat
            await session.execute(
                update(JobAttemptRecord)
                .where(
                    JobAttemptRecord.job_id == job_id,
                    JobAttemptRecord.attempt_no == attempt_no,
                    JobAttemptRecord.fencing_token == fencing_token,
                )
                .values(heartbeat_at=heartbeat_at, lease_expires_at=lease_expires_at)
            )
            return True
