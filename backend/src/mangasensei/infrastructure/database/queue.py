"""Atomic PostgreSQL queue statements."""

from __future__ import annotations

from sqlalchemy import Update, func, select, text, update

from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import PageRecord


def build_claim_statement(*, worker_id: str, lease_seconds: int) -> Update:
    if not worker_id or len(worker_id) > 128:
        raise ValueError("worker_id must contain between 1 and 128 characters")
    if not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")

    eligible = (
        select(JobRecord.id)
        .join(PageRecord, PageRecord.id == JobRecord.page_id)
        .where(
            text("mangasensei.jobs.status IN ('pending','retryable_failure')"),
            JobRecord.available_at <= func.now(),
            JobRecord.attempt_count < JobRecord.max_attempts,
            PageRecord.expires_at > func.now(),
        )
        .order_by(JobRecord.available_at, JobRecord.id)
        .limit(1)
        .with_for_update(of=JobRecord, skip_locked=True)
        .cte("eligible_job")
    )
    lease_interval = func.make_interval(0, 0, 0, 0, 0, 0, lease_seconds)
    return (
        update(JobRecord)
        .where(JobRecord.id == eligible.c.id)
        .values(
            status="claimed",
            worker_id=worker_id,
            attempt_count=JobRecord.attempt_count + 1,
            fencing_token=JobRecord.fencing_token + 1,
            claimed_at=func.now(),
            heartbeat_at=func.now(),
            lease_expires_at=func.now() + lease_interval,
            started_at=func.coalesce(JobRecord.started_at, func.now()),
            updated_at=func.now(),
            error_code=None,
            error_detail=None,
        )
        .returning(JobRecord)
    )
