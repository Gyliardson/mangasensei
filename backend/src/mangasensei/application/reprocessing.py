"""Idempotent page reprocessing under a dedicated capability scope."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.application.authorization import ResourceNotFoundError
from mangasensei.application.idempotency import idempotency_digest
from mangasensei.domain.languages import (
    DEFAULT_STUDY_LANGUAGE,
    DictionaryLanguage,
    StudyLanguage,
)
from mangasensei.infrastructure.database.dictionary_projection_models import (
    DictionaryProjectionRequestRecord,
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
_REPROCESS_KINDS = (
    "page_reprocess",
    "study_language_reprocess",
    "dictionary_language_reprocess",
)


class AnalysisInProgressError(RuntimeError):
    """A page already has an active analysis job."""


class DictionaryProjectionUnavailableError(RuntimeError):
    """Dictionary reprojection requires one completed canonical linguistic result."""


class ReprocessIdempotencyConflictError(ValueError):
    """A reprocess idempotency key was reused with a different request contract."""


@dataclass(frozen=True, slots=True)
class ReprocessResult:
    job_id: UUID
    status: str
    study_language: str
    created: bool


@dataclass(frozen=True, slots=True)
class DictionaryReprocessResult:
    job_id: UUID
    status: str
    study_language: str
    requested_dictionary_language: str
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

    async def create(
        self,
        *,
        page_id: int,
        idempotency_key: str,
        study_language: StudyLanguage | None = None,
    ) -> ReprocessResult:
        digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="reprocess",
            value=idempotency_key,
        )
        async with self._sessions.begin() as session:
            page = await _lock_live_page(session, page_id)
            latest_result = await _latest_completed_result(session, page.id)
            requested_language = (
                study_language
                if study_language is not None
                else StudyLanguage(latest_result.study_language)
                if latest_result is not None
                else DEFAULT_STUDY_LANGUAGE
            )
            job_kind = (
                "study_language_reprocess"
                if study_language is not None and latest_result is not None
                else "page_reprocess"
            )

            existing = await _existing_reprocess_job(session, page.id, digest)
            if existing is not None:
                if (
                    existing.job_kind != job_kind
                    or existing.study_language != requested_language.value
                ):
                    raise ReprocessIdempotencyConflictError(
                        "idempotency key is bound to another reprocess request"
                    )
                return ReprocessResult(
                    job_id=existing.public_id,
                    status=existing.status,
                    study_language=existing.study_language,
                    created=False,
                )
            await _require_no_active_job(session, page.id)
            job = JobRecord(
                page_id=page.id,
                job_kind=job_kind,
                idempotency_digest=digest,
                request_digest=page.request_digest,
                study_language=requested_language.value,
            )
            session.add(job)
            await session.flush()
            return ReprocessResult(
                job_id=job.public_id,
                status=job.status,
                study_language=job.study_language,
                created=True,
            )

    async def create_dictionary_projection(
        self,
        *,
        page_id: int,
        idempotency_key: str,
        dictionary_language: DictionaryLanguage,
    ) -> DictionaryReprocessResult:
        digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="reprocess",
            value=idempotency_key,
        )
        async with self._sessions.begin() as session:
            page = await _lock_live_page(session, page_id)
            latest_result = await _latest_completed_result(session, page.id)
            if latest_result is None:
                raise DictionaryProjectionUnavailableError

            existing = await _existing_reprocess_job(session, page.id, digest)
            if existing is not None:
                request = await session.get(DictionaryProjectionRequestRecord, existing.id)
                if (
                    existing.job_kind != "dictionary_language_reprocess"
                    or request is None
                    or request.requested_dictionary_language != dictionary_language.value
                ):
                    raise ReprocessIdempotencyConflictError(
                        "idempotency key is bound to another reprocess request"
                    )
                return DictionaryReprocessResult(
                    job_id=existing.public_id,
                    status=existing.status,
                    study_language=existing.study_language,
                    requested_dictionary_language=request.requested_dictionary_language,
                    created=False,
                )

            await _require_no_active_job(session, page.id)
            job = JobRecord(
                page_id=page.id,
                job_kind="dictionary_language_reprocess",
                idempotency_digest=digest,
                request_digest=page.request_digest,
                study_language=latest_result.study_language,
            )
            session.add(job)
            await session.flush()
            session.add(
                DictionaryProjectionRequestRecord(
                    job_id=job.id,
                    requested_dictionary_language=dictionary_language.value,
                )
            )
            return DictionaryReprocessResult(
                job_id=job.public_id,
                status=job.status,
                study_language=job.study_language,
                requested_dictionary_language=dictionary_language.value,
                created=True,
            )


async def _lock_live_page(session: AsyncSession, page_id: int) -> PageRecord:
    page = (
        await session.execute(
            select(PageRecord)
            .where(PageRecord.id == page_id, PageRecord.expires_at > func.now())
            .with_for_update()
        )
    ).scalar_one_or_none()
    if page is None:
        raise ResourceNotFoundError
    return page


async def _latest_completed_result(
    session: AsyncSession,
    page_id: int,
) -> StudyResultRecord | None:
    return (
        await session.execute(
            select(StudyResultRecord)
            .join(JobRecord, JobRecord.id == StudyResultRecord.job_id)
            .where(JobRecord.page_id == page_id, JobRecord.status == "completed")
            .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _existing_reprocess_job(
    session: AsyncSession,
    page_id: int,
    digest: bytes,
) -> JobRecord | None:
    return (
        await session.execute(
            select(JobRecord).where(
                JobRecord.page_id == page_id,
                JobRecord.job_kind.in_(_REPROCESS_KINDS),
                JobRecord.idempotency_digest == digest,
            )
        )
    ).scalar_one_or_none()


async def _require_no_active_job(session: AsyncSession, page_id: int) -> None:
    active = (
        await session.execute(
            select(JobRecord.id).where(
                JobRecord.page_id == page_id,
                JobRecord.status.in_(_ACTIVE_STATUSES),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise AnalysisInProgressError
