"""Read projection for document aggregates and page progress."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord

_PROCESSING_STATUSES = {
    "pending",
    "claimed",
    "processing_ocr",
    "processing_linguistics",
    "processing_gemini",
    "retryable_failure",
}


class DocumentQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, document_id: int) -> dict[str, Any]:
        pages, progress, aggregate_status = await self._project(document_id)
        return {"status": aggregate_status, "pages": pages, "progress": progress}

    async def get_progress(self, document_id: int) -> dict[str, Any]:
        _, progress, aggregate_status = await self._project(document_id)
        return {"status": aggregate_status, **progress}

    async def _project(
        self, document_id: int
    ) -> tuple[list[dict[str, Any]], dict[str, int], str]:
        async with self._sessions() as session:
            page_rows = tuple(
                (
                    await session.execute(
                        select(PageRecord.id, PageRecord.public_id, PageRecord.ordinal)
                        .where(PageRecord.document_id == document_id)
                        .order_by(PageRecord.ordinal, PageRecord.id)
                    )
                ).all()
            )
            if not page_rows:
                progress = _progress(
                    total=0,
                    completed=0,
                    processing=0,
                    failed=0,
                    cancelled=0,
                )
                return [], progress, "completed"

            page_ids = tuple(row.id for row in page_rows)
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
            completed_page_ids = set(
                (
                    await session.execute(
                        select(JobRecord.page_id)
                        .join(StudyResultRecord, StudyResultRecord.job_id == JobRecord.id)
                        .where(JobRecord.page_id.in_(page_ids))
                        .distinct()
                    )
                ).scalars()
            )

        latest_by_page: dict[int, JobRecord] = {}
        for job in jobs:
            latest_by_page.setdefault(job.page_id, job)

        completed = 0
        processing = 0
        failed = 0
        cancelled = 0
        active_work = False
        pages: list[dict[str, Any]] = []
        for row in page_rows:
            latest_job = latest_by_page.get(row.id)
            if latest_job is None:
                raise RuntimeError("document page has no analysis job")
            result_available = row.id in completed_page_ids
            active_work = active_work or latest_job.status in _PROCESSING_STATUSES
            if result_available:
                completed += 1
            elif latest_job.status in _PROCESSING_STATUSES:
                processing += 1
            elif latest_job.status == "failed":
                failed += 1
            elif latest_job.status == "cancelled":
                cancelled += 1
            else:
                raise RuntimeError(
                    f"document page has unclassifiable latest job status: {latest_job.status}"
                )
            pages.append(
                {
                    "pageId": str(row.public_id),
                    "ordinal": row.ordinal,
                    "status": latest_job.status,
                    "resultAvailable": result_available,
                }
            )

        total = len(page_rows)
        if completed + processing + failed + cancelled != total:
            raise RuntimeError("document progress counters do not partition page membership")
        progress = _progress(
            total=total,
            completed=completed,
            processing=processing,
            failed=failed,
            cancelled=cancelled,
        )
        if active_work:
            aggregate_status = "processing"
        elif cancelled:
            aggregate_status = "cancelled"
        elif failed:
            aggregate_status = "completed_with_errors"
        else:
            aggregate_status = "completed"
        return pages, progress, aggregate_status


def _progress(
    *,
    total: int,
    completed: int,
    processing: int,
    failed: int,
    cancelled: int,
) -> dict[str, int]:
    return {
        "totalPages": total,
        "completedPages": completed,
        "processingPages": processing,
        "failedPages": failed,
        "cancelledPages": cancelled,
    }
