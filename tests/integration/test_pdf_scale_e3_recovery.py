from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from mangasensei.api.app import create_app
from mangasensei.application.pdf_imports import PdfImportCoordinator
from mangasensei.config import Settings
from mangasensei.infrastructure.database.document_import_models import DocumentImportRecord
from mangasensei.infrastructure.database.document_models import DocumentRecord
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.pdf_imports.renderer import PdfRenderer
from mangasensei.pdf_imports.spool import PdfSpool
from mangasensei.storage.images import ImageValidator
from mangasensei.storage.local import LocalFilesystemStorage
from tests.pdf_scale.generator import generate_pdf
from tests.pdf_scale.runtime_common import write_json

_PEPPER = "pdf-e3-postcommit-integration-pepper-0001"


class _SimulatedPostCommitProcessDeath(RuntimeError):
    pass


class _CrashBeforeTerminalCleanup(PdfImportCoordinator):
    async def _cleanup_terminal_source(self, import_id: UUID) -> None:
        del import_id
        raise _SimulatedPostCommitProcessDeath


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url.replace("postgresql://", "postgresql+psycopg://", 1),
        storage_root=root / "storage",
        pdf_spool_root=root / "pdf-spool",
        model_cache=root / "models",
        capability_peppers=(_PEPPER,),
    )


def _coordinator(
    cls: type[PdfImportCoordinator],
    settings: Settings,
    sessions,
    *,
    worker_id: str,
) -> PdfImportCoordinator:
    return cls(
        sessions=sessions,
        storage=LocalFilesystemStorage(settings.storage_root),
        spool=PdfSpool(settings.pdf_spool_root),
        image_validator=ImageValidator(
            max_bytes=settings.max_upload_bytes,
            max_pixels=settings.max_image_pixels,
            max_side=settings.max_image_side,
        ),
        settings=settings,
        idempotency_pepper=_PEPPER,
        worker_id=worker_id,
    )


async def _admit(settings: Settings) -> UUID:
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": "pdf-e3-postcommit-v1"},
            data={"studyLanguage": "en"},
            files={"pdf": ("pdf-pagecount-max-200.pdf", generate_pdf(), "application/pdf")},
        )
    assert response.status_code == 202
    return UUID(response.json()["data"]["importId"])


async def _run_render_to_simulated_death(
    settings: Settings,
    coordinator: PdfImportCoordinator,
) -> None:
    task = asyncio.create_task(coordinator.run_once())
    request_dir = settings.pdf_spool_root / "requests"
    for _ in range(400):
        if request_dir.exists() and any(request_dir.glob("*.request.json")):
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        raise AssertionError("coordinator did not publish the E3 renderer request")
    await asyncio.to_thread(PdfRenderer(settings).run_once)
    with pytest.raises(_SimulatedPostCommitProcessDeath):
        await task


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pdf_e3_postcommit_cleanup_recovery_preserves_200_page_document(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    import_id = await _admit(settings)
    engine, sessions = create_database(settings.require_database_url())
    crashing = _coordinator(
        _CrashBeforeTerminalCleanup,
        settings,
        sessions,
        worker_id="e3-postcommit-crash",
    )
    started = time.perf_counter()
    try:
        await _run_render_to_simulated_death(settings, crashing)
        elapsed = time.perf_counter() - started
        spool = PdfSpool(settings.pdf_spool_root)
        async with sessions() as session:
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            before = {
                "documents": await session.scalar(select(func.count(DocumentRecord.id))),
                "pages": await session.scalar(select(func.count(PageRecord.id))),
                "jobs": await session.scalar(select(func.count(JobRecord.id))),
                "status": record.status,
                "fencingToken": record.fencing_token,
                "sourceCleaned": record.source_cleaned_at is not None,
            }
        assert before == {
            "documents": 1,
            "pages": 200,
            "jobs": 200,
            "status": "completed",
            "fencingToken": 1,
            "sourceCleaned": False,
        }
        assert spool.source_path(import_id).is_file()
        assert spool.attempt_dir(import_id, 1).is_dir()

        recovery = _coordinator(
            PdfImportCoordinator,
            settings,
            sessions,
            worker_id="e3-postcommit-recovery",
        )
        await recovery.cleanup_once()

        async with sessions() as session:
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            after = {
                "documents": await session.scalar(select(func.count(DocumentRecord.id))),
                "pages": await session.scalar(select(func.count(PageRecord.id))),
                "jobs": await session.scalar(select(func.count(JobRecord.id))),
                "status": record.status,
                "fencingToken": record.fencing_token,
                "sourceCleaned": record.source_cleaned_at is not None,
            }
        assert after == {
            "documents": 1,
            "pages": 200,
            "jobs": 200,
            "status": "completed",
            "fencingToken": 1,
            "sourceCleaned": True,
        }
        assert not spool.import_dir(import_id).exists()
        assert not spool.output_import_dir(import_id).exists()
        assert not any(spool.requests.glob(f"{import_id}.*.request.json"))

        evidence_root = os.environ.get("MANGASENSEI_E3_EVIDENCE_ROOT")
        source_sha = os.environ.get("MANGASENSEI_E3_SOURCE_SHA")
        if evidence_root and source_sha:
            write_json(
                Path(evidence_root) / "recovery-postcommit-cleanup.json",
                {
                    "schemaVersion": 1,
                    "scenario": "postcommit-precleanup-recovery",
                    "repositorySourceSha": source_sha,
                    "importId": str(import_id),
                    "simulatedDeathAfterDatabaseCommit": True,
                    "commitWasNotMocked": True,
                    "beforeRecovery": before,
                    "sourceAndAttemptExistedBeforeRecovery": True,
                    "afterRecovery": after,
                    "terminalSpoolRemoved": True,
                    "elapsedSeconds": elapsed,
                    "result": "pass",
                },
            )
    finally:
        await engine.dispose()
