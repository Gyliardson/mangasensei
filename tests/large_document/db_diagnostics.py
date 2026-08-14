"""Database diagnostics for the deterministic Slice E1 full-stack scenario."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from mangasensei.application.document_queries import DocumentQueryService
from mangasensei.config import Settings
from mangasensei.infrastructure.database.analysis_models import OcrRunRecord
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from tests.large_document.generator import PAGE_COUNT


def _load_document_id(path: Path) -> UUID:
    payload = json.loads(path.read_text(encoding="utf-8"))
    document_id = payload.get("documentId")
    if not isinstance(document_id, str):
        raise ValueError("document marker is missing documentId")
    return UUID(document_id)


async def _projection_metrics(
    engine: AsyncEngine,
    document_id: int,
    sessions: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    statements: list[tuple[str, int]] = []

    def after_cursor_execute(
        _conn: Connection,
        cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append((statement, cursor.rowcount))

    event.listen(engine.sync_engine, "after_cursor_execute", after_cursor_execute)
    started = time.perf_counter()
    try:
        projection = await DocumentQueryService(sessions).get(document_id)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        event.remove(engine.sync_engine, "after_cursor_execute", after_cursor_execute)

    job_rowcounts = [
        rowcount
        for statement, rowcount in statements
        if "FROM mangasensei.jobs" in statement
        and "ORDER BY mangasensei.jobs.page_id" in statement
        and rowcount >= 0
    ]
    job_rows_source = "cursor.rowcount"
    if job_rowcounts:
        job_rows_loaded = max(job_rowcounts)
    else:
        async with sessions() as session:
            page_ids = tuple(
                (
                    await session.execute(
                        select(PageRecord.id).where(PageRecord.document_id == document_id)
                    )
                ).scalars()
            )
            job_rows_loaded = len(
                tuple(
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
            )
        job_rows_source = "equivalent_projection_query_fallback"

    return projection, {
        "sqlStatementCount": len(statements),
        "jobRowsLoaded": job_rows_loaded,
        "jobRowsLoadedSource": job_rows_source,
        "elapsedMs": round(elapsed_ms, 3),
    }


async def collect(phase: str, marker: Path) -> dict[str, Any]:
    settings = Settings(_env_file=None)
    engine, sessions = create_database(settings.require_database_url())
    try:
        public_document_id = _load_document_id(marker)
        async with sessions() as session:
            document = (
                await session.execute(
                    select(DocumentRecord).where(DocumentRecord.public_id == public_document_id)
                )
            ).scalar_one()
            page_rows = tuple(
                (
                    await session.execute(
                        select(
                            PageRecord.id,
                            PageRecord.ordinal,
                            PageRecord.image_blob_id,
                            PageRecord.original_filename,
                        )
                        .where(PageRecord.document_id == document.id)
                        .order_by(PageRecord.ordinal, PageRecord.id)
                    )
                ).all()
            )
            page_ids = tuple(row.id for row in page_rows)
            total_documents = await session.scalar(select(func.count()).select_from(DocumentRecord))
            job_count = await session.scalar(
                select(func.count()).select_from(JobRecord).where(JobRecord.page_id.in_(page_ids))
            )
            attempt_count = await session.scalar(
                select(func.count())
                .select_from(JobAttemptRecord)
                .join(JobRecord, JobRecord.id == JobAttemptRecord.job_id)
                .where(JobRecord.page_id.in_(page_ids))
            )
            study_result_count = await session.scalar(
                select(func.count())
                .select_from(StudyResultRecord)
                .join(JobRecord, JobRecord.id == StudyResultRecord.job_id)
                .where(JobRecord.page_id.in_(page_ids))
            )
            image_blob_count = await session.scalar(
                select(func.count(func.distinct(PageRecord.image_blob_id))).where(
                    PageRecord.document_id == document.id
                )
            )
            capability_count = await session.scalar(
                select(func.count())
                .select_from(DocumentCapabilityRecord)
                .where(DocumentCapabilityRecord.document_id == document.id)
            )
            ocr_input_matches = await session.scalar(
                select(func.count())
                .select_from(OcrRunRecord)
                .join(JobRecord, JobRecord.id == OcrRunRecord.job_id)
                .join(PageRecord, PageRecord.id == JobRecord.page_id)
                .join(ImageBlobRecord, ImageBlobRecord.id == PageRecord.image_blob_id)
                .where(
                    PageRecord.document_id == document.id,
                    OcrRunRecord.input_sha256 == ImageBlobRecord.sha256,
                )
            )

        projection, projection_metrics = await _projection_metrics(engine, document.id, sessions)
        db = {
            "documents": int(total_documents or 0),
            "pages": len(page_rows),
            "jobs": int(job_count or 0),
            "jobAttempts": int(attempt_count or 0),
            "studyResults": int(study_result_count or 0),
            "imageBlobs": int(image_blob_count or 0),
            "documentCapabilities": int(capability_count or 0),
            "ocrInputBlobAssociations": int(ocr_input_matches or 0),
            "ordinals": [row.ordinal for row in page_rows],
            "filenames": [row.original_filename for row in page_rows],
        }
        result = {
            "schemaVersion": 1,
            "phase": phase,
            "documentId": str(public_document_id),
            "db": db,
            "progress": projection["progress"],
            "aggregateStatus": projection["status"],
            "aggregateProjection": projection_metrics,
        }
        _assert_phase_contract(result)
        return result
    finally:
        await engine.dispose()


def _assert_phase_contract(result: dict[str, Any]) -> None:
    phase = result["phase"]
    db = result["db"]
    expected_base = {
        "documents": 1,
        "pages": PAGE_COUNT,
        "jobs": PAGE_COUNT,
        "imageBlobs": PAGE_COUNT,
        "documentCapabilities": 4,
    }
    for key, expected in expected_base.items():
        if db[key] != expected:
            raise RuntimeError(f"{phase} {key} expected {expected}, observed {db[key]}")
    if db["ordinals"] != list(range(PAGE_COUNT)):
        raise RuntimeError(f"{phase} page ordering is not exactly 0..199")
    if db["filenames"] != [f"page-{ordinal + 1:06d}.png" for ordinal in range(PAGE_COUNT)]:
        raise RuntimeError(f"{phase} durable filenames differ from the frozen workload")
    if result["aggregateProjection"]["sqlStatementCount"] != 3:
        raise RuntimeError("aggregate projection must remain a three-statement set-oriented read")
    if result["aggregateProjection"]["jobRowsLoaded"] != PAGE_COUNT:
        raise RuntimeError("aggregate projection did not load exactly the 200 initial Page jobs")

    if phase == "initial":
        expected_progress = {
            "totalPages": PAGE_COUNT,
            "completedPages": 0,
            "processingPages": PAGE_COUNT,
            "failedPages": 0,
            "cancelledPages": 0,
        }
        expected_terminal = {"jobAttempts": 0, "studyResults": 0, "ocrInputBlobAssociations": 0}
        expected_status = "processing"
    elif phase == "final":
        expected_progress = {
            "totalPages": PAGE_COUNT,
            "completedPages": PAGE_COUNT,
            "processingPages": 0,
            "failedPages": 0,
            "cancelledPages": 0,
        }
        expected_terminal = {
            "jobAttempts": PAGE_COUNT,
            "studyResults": PAGE_COUNT,
            "ocrInputBlobAssociations": PAGE_COUNT,
        }
        expected_status = "completed"
    else:
        raise ValueError(f"unsupported diagnostics phase: {phase}")

    if result["progress"] != expected_progress:
        raise RuntimeError(
            f"{phase} progress expected {expected_progress!r}, observed {result['progress']!r}"
        )
    if result["aggregateStatus"] != expected_status:
        raise RuntimeError(
            f"{phase} aggregate status expected {expected_status!r}, "
            f"observed {result['aggregateStatus']!r}"
        )
    for key, expected in expected_terminal.items():
        if db[key] != expected:
            raise RuntimeError(f"{phase} {key} expected {expected}, observed {db[key]}")


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("initial", "final"), required=True)
    parser.add_argument("--document-marker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = await collect(args.phase, args.document_marker)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "phase": result["phase"],
                "db": {
                    key: value
                    for key, value in result["db"].items()
                    if key not in {"ordinals", "filenames"}
                },
                "progress": result["progress"],
                "aggregateProjection": result["aggregateProjection"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(_main())
