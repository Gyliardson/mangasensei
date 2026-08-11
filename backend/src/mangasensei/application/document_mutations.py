"""Bounded document recovery, cancellation and persisted order mutations."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.application.authorization import ResourceNotFoundError
from mangasensei.application.idempotency import idempotency_digest
from mangasensei.infrastructure.database.document_models import (
    DocumentRecord,
    DocumentRetryRequestRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord

_ACTIVE_STATUSES = (
    "pending",
    "claimed",
    "processing_ocr",
    "processing_linguistics",
    "processing_gemini",
    "retryable_failure",
)
_LEASED_STATUSES = (
    "claimed",
    "processing_ocr",
    "processing_linguistics",
    "processing_gemini",
)


class DocumentOrderConflictError(RuntimeError):
    """The optimistic order revision no longer matches."""


class DocumentOrderMembershipError(ValueError):
    """A reorder request does not contain the exact Document membership set."""


@dataclass(frozen=True, slots=True)
class RetryFailedResult:
    created: bool
    page_ids: tuple[UUID, ...]
    job_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class CancelDocumentResult:
    cancelled_pages: int
    cancel_requested_pages: int


@dataclass(frozen=True, slots=True)
class ReorderDocumentResult:
    order_revision: int


class DocumentMutationService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        idempotency_pepper: str,
        max_pages: int,
    ) -> None:
        if max_pages < 1:
            raise ValueError("max_pages must be positive")
        self._sessions = sessions
        self._idempotency_pepper = idempotency_pepper.encode()
        self._max_pages = max_pages

    async def retry_failed(
        self,
        *,
        document_id: int,
        idempotency_key: str,
    ) -> RetryFailedResult:
        digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="document-retry-failed",
            value=idempotency_key,
        )
        async with self._sessions.begin() as session:
            await _lock_live_document(session, document_id)
            pages = await _lock_member_pages(session, document_id)
            if len(pages) > self._max_pages:
                raise RuntimeError("document exceeds configured recovery bound")

            existing_request = (
                await session.execute(
                    select(DocumentRetryRequestRecord).where(
                        DocumentRetryRequestRecord.document_id == document_id,
                        DocumentRetryRequestRecord.idempotency_digest == digest,
                    )
                )
            ).scalar_one_or_none()
            if existing_request is not None:
                rows = (
                    await session.execute(
                        select(JobRecord, PageRecord.public_id)
                        .join(PageRecord, PageRecord.id == JobRecord.page_id)
                        .where(JobRecord.document_retry_request_id == existing_request.id)
                        .order_by(PageRecord.ordinal, JobRecord.id)
                    )
                ).all()
                return RetryFailedResult(
                    created=False,
                    page_ids=tuple(public_id for _, public_id in rows),
                    job_ids=tuple(job.public_id for job, _ in rows),
                )

            retry_request = DocumentRetryRequestRecord(
                document_id=document_id,
                idempotency_digest=digest,
            )
            session.add(retry_request)
            await session.flush()

            page_ids = tuple(page.id for page in pages)
            jobs = tuple(
                (
                    await session.execute(
                        select(JobRecord)
                        .where(JobRecord.page_id.in_(page_ids))
                        .order_by(
                            JobRecord.page_id,
                            JobRecord.created_at.desc(),
                            JobRecord.id.desc(),
                        )
                    )
                ).scalars()
            )
            latest_by_page: dict[int, JobRecord] = {}
            for job in jobs:
                latest_by_page.setdefault(job.page_id, job)
            result_page_ids = set(
                (
                    await session.execute(
                        select(JobRecord.page_id)
                        .join(StudyResultRecord, StudyResultRecord.job_id == JobRecord.id)
                        .where(JobRecord.page_id.in_(page_ids))
                        .distinct()
                    )
                ).scalars()
            )

            retried_pages: list[UUID] = []
            retry_jobs: list[UUID] = []
            for page in pages:
                latest = latest_by_page.get(page.id)
                if latest is None:
                    raise RuntimeError("document page has no analysis job")
                if page.id in result_page_ids or latest.status != "failed":
                    continue
                job = JobRecord(
                    page_id=page.id,
                    document_retry_request_id=retry_request.id,
                    job_kind="document_retry",
                    idempotency_digest=self._retry_job_digest(digest, page.id),
                    request_digest=page.request_digest,
                    study_language=latest.study_language,
                )
                session.add(job)
                await session.flush()
                retried_pages.append(page.public_id)
                retry_jobs.append(job.public_id)

            return RetryFailedResult(
                created=bool(retry_jobs),
                page_ids=tuple(retried_pages),
                job_ids=tuple(retry_jobs),
            )

    async def cancel(self, *, document_id: int) -> CancelDocumentResult:
        async with self._sessions.begin() as session:
            await _lock_live_document(session, document_id)
            pages = await _lock_member_pages(session, document_id)
            page_ids = tuple(page.id for page in pages)
            if not page_ids:
                return CancelDocumentResult(cancelled_pages=0, cancel_requested_pages=0)
            jobs = tuple(
                (
                    await session.execute(
                        select(JobRecord)
                        .where(
                            JobRecord.page_id.in_(page_ids),
                            JobRecord.status.in_(_ACTIVE_STATUSES),
                        )
                        .order_by(JobRecord.page_id, JobRecord.id)
                        .with_for_update()
                    )
                ).scalars()
            )
            cancelled = 0
            requested = 0
            for job in jobs:
                if job.status in {"pending", "retryable_failure"}:
                    job.cancel_requested_at = func.now()
                    job.status = "cancelled"
                    job.finished_at = func.now()
                    job.error_code = None
                    job.error_detail = None
                    job.updated_at = func.now()
                    cancelled += 1
                elif job.status in _LEASED_STATUSES and job.cancel_requested_at is None:
                    job.cancel_requested_at = func.now()
                    job.updated_at = func.now()
                    requested += 1
            return CancelDocumentResult(
                cancelled_pages=cancelled,
                cancel_requested_pages=requested,
            )

    async def reorder(
        self,
        *,
        document_id: int,
        ordered_page_ids: tuple[UUID, ...],
        expected_order_revision: int,
    ) -> ReorderDocumentResult:
        async with self._sessions.begin() as session:
            document = await _lock_live_document(session, document_id)
            if document.order_revision != expected_order_revision:
                raise DocumentOrderConflictError
            pages = await _lock_member_pages(session, document_id)
            if len(pages) > self._max_pages:
                raise RuntimeError("document exceeds configured reorder bound")
            if len(ordered_page_ids) != len(pages) or len(set(ordered_page_ids)) != len(
                ordered_page_ids
            ):
                raise DocumentOrderMembershipError
            by_public_id = {page.public_id: page for page in pages}
            if set(ordered_page_ids) != set(by_public_id):
                raise DocumentOrderMembershipError
            if tuple(page.public_id for page in pages) == ordered_page_ids:
                return ReorderDocumentResult(order_revision=document.order_revision)

            temporary_base = max((page.ordinal or 0) for page in pages) + len(pages) + 1
            for index, page_id in enumerate(ordered_page_ids):
                by_public_id[page_id].ordinal = temporary_base + index
            await session.flush()
            for ordinal, page_id in enumerate(ordered_page_ids):
                by_public_id[page_id].ordinal = ordinal
            document.order_revision += 1
            await session.flush()
            return ReorderDocumentResult(order_revision=document.order_revision)

    def _retry_job_digest(self, retry_digest: bytes, page_id: int) -> bytes:
        message = (
            b"mangasensei:document-retry-job:v1\0"
            + retry_digest
            + page_id.to_bytes(8, "big")
        )
        return hmac.new(self._idempotency_pepper, message, hashlib.sha256).digest()


async def _lock_live_document(session: AsyncSession, document_id: int) -> DocumentRecord:
    document = (
        await session.execute(
            select(DocumentRecord)
            .where(DocumentRecord.id == document_id, DocumentRecord.expires_at > func.now())
            .with_for_update()
        )
    ).scalar_one_or_none()
    if document is None:
        raise ResourceNotFoundError
    return document


async def _lock_member_pages(session: AsyncSession, document_id: int) -> tuple[PageRecord, ...]:
    return tuple(
        (
            await session.execute(
                select(PageRecord)
                .where(
                    PageRecord.document_id == document_id,
                    PageRecord.expires_at > func.now(),
                )
                .order_by(PageRecord.ordinal, PageRecord.id)
                .with_for_update()
            )
        ).scalars()
    )
