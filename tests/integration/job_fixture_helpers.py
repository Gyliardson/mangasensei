from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from mangasensei.infrastructure.database.job_models import JobRecord


async def advance_pending_job_to_processing_linguistics(
    session: AsyncSession,
    job: JobRecord,
) -> None:
    """Advance a fixture job through the real persisted transition contract."""

    assert job.status == "pending"
    now = datetime.now(UTC)
    job.status = "claimed"
    job.worker_id = "slice-b-fixture-worker"
    job.claimed_at = now
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(minutes=5)
    job.attempt_count = max(job.attempt_count, 1)
    job.fencing_token = max(job.fencing_token, 1)
    await session.flush()

    job.status = "processing_ocr"
    job.started_at = now
    await session.flush()

    job.status = "processing_linguistics"
    await session.flush()


async def finish_processing_job(
    session: AsyncSession,
    job: JobRecord,
) -> None:
    """Finish a fixture job without bypassing the persisted transition trigger."""

    assert job.status in {"processing_linguistics", "processing_gemini"}
    job.status = "completed"
    job.finished_at = datetime.now(UTC)
    job.worker_id = None
    job.heartbeat_at = None
    job.lease_expires_at = None
    await session.flush()
