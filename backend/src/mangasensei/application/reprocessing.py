"""Idempotent page reprocessing under a dedicated capability scope."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.application.authorization import ResourceNotFoundError
from mangasensei.application.idempotency import idempotency_digest
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import PageRecord

_ACTIVE_STATUSES = (
    "pending",
    "claimed",
    "processing_ocr",
    "processing_linguistics",
    "processing_gemini",
    "retryable_failure",
)


class AnalysisInProgressError(RuntimeError):
    """A page already has an active analysis job."""


@dataclass(frozen=True, slots=True)
class ReprocessResult:
    job_id: UUID
    status: str
    created: bool


class ReprocessService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        idempotency_pepper: str,
    ) -> None:
        self._sessions = sessions
        self._idempotency_pepper = idempotency_pepper.encode()

    async def create(self, *, page_id: int, idempotency_key: str) -> ReprocessResult:
        digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="reprocess",
            value=idempotency_key,
        )
        async with self._sessions.begin() as session:
            page = (
                await session.execute(
                    select(PageRecord)
                    .where(PageRecord.id == page_id, PageRecord.expires_at > func.now())
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if page is None:
                raise ResourceNotFoundError
            existing = (
                await session.execute(
                    select(JobRecord).where(
                        JobRecord.page_id == page.id,
                        JobRecord.job_kind == "page_reprocess",
                        JobRecord.idempotency_digest == digest,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return ReprocessResult(
                    job_id=existing.public_id,
                    status=existing.status,
                    created=False,
                )
            active = (
                await session.execute(
                    select(JobRecord.id).where(
                        JobRecord.page_id == page.id,
                        JobRecord.status.in_(_ACTIVE_STATUSES),
                    )
                )
            ).scalar_one_or_none()
            if active is not None:
                raise AnalysisInProgressError
            job = JobRecord(
                page_id=page.id,
                job_kind="page_reprocess",
                idempotency_digest=digest,
                request_digest=page.request_digest,
            )
            session.add(job)
            await session.flush()
            return ReprocessResult(job_id=job.public_id, status=job.status, created=True)
