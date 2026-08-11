from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

import pypdfium2 as pdfium
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
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.pdf_imports.renderer import PdfRenderer
from mangasensei.pdf_imports.spool import PdfSpool
from mangasensei.storage.images import ImageValidator
from mangasensei.storage.local import LocalFilesystemStorage

_PEPPER = "pdf-integration-test-pepper-value-0001"


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url.replace("postgresql://", "postgresql+psycopg://", 1),
        storage_root=root / "storage",
        pdf_spool_root=root / "pdf-spool",
        model_cache=root / "models",
        capability_peppers=(_PEPPER,),
    )


def _pdf_bytes(*, pages: int = 1) -> bytes:
    path = Path.cwd() / ".pytest-pdf-import-fixture.pdf"
    document = pdfium.PdfDocument.new()
    try:
        for _ in range(pages):
            page = document.new_page(72, 72)
            page.close()
        document.save(path)
    finally:
        document.close()
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


async def _run_actual_render_boundary(
    settings: Settings,
    coordinator: PdfImportCoordinator,
) -> None:
    task = asyncio.create_task(coordinator.run_once())
    request_dir = settings.pdf_spool_root / "requests"
    for _ in range(200):
        if request_dir.exists() and any(request_dir.glob("*.request.json")):
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        raise AssertionError("coordinator did not publish a renderer request")

    await asyncio.to_thread(PdfRenderer(settings).run_once)
    assert await task is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pdf_import_is_atomic_reuses_blob_and_enters_normal_page_jobs(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    app = create_app(settings)
    content = _pdf_bytes(pages=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": "pdf-two-pages"},
            data={"studyLanguage": "en"},
            files={"pdf": ("volume.pdf", content, "application/pdf")},
        )
        assert accepted.status_code == 202
        admitted = accepted.json()["data"]
        import_id = UUID(admitted["importId"])
        import_token = admitted["capabilities"]["readDocumentImport"]

        before = await client.get(
            f"/api/v1/document-imports/{import_id}",
            headers={"X-Document-Import-Token": import_token},
        )
        assert before.status_code == 200
        assert before.json()["data"]["document"] is None
        assert before.json()["data"]["status"] == "queued"

        engine, sessions = create_database(settings.require_database_url())
        coordinator = PdfImportCoordinator(
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
            worker_id="pdf-import-test",
        )
        try:
            async with sessions() as session:
                assert await session.scalar(select(func.count(DocumentRecord.id))) == 0
                assert await session.scalar(select(func.count(PageRecord.id))) == 0
            await _run_actual_render_boundary(settings, coordinator)
            async with sessions() as session:
                document = (await session.execute(select(DocumentRecord))).scalar_one()
                pages = tuple(
                    (
                        await session.execute(
                            select(PageRecord).order_by(PageRecord.ordinal, PageRecord.id)
                        )
                    ).scalars()
                )
                assert document.source_kind == "pdf"
                assert [page.ordinal for page in pages] == [0, 1]
                assert pages[0].image_blob_id == pages[1].image_blob_id
                assert await session.scalar(select(func.count(ImageBlobRecord.id))) == 1
                assert await session.scalar(select(func.count(JobRecord.id))) == 2
                record = (await session.execute(select(DocumentImportRecord))).scalar_one()
                assert record.status == "completed"
                assert record.document_id == document.id
                assert record.page_count == 2
                assert record.source_cleaned_at is not None
        finally:
            await engine.dispose()

        after = await client.get(
            f"/api/v1/document-imports/{import_id}",
            headers={"X-Document-Import-Token": import_token},
        )
        assert after.status_code == 200
        completed = after.json()["data"]
        assert completed["status"] == "completed"
        assert completed["pageCount"] == 2
        assert completed["errorCode"] is None
        assert completed["document"] is not None
        document_id = completed["document"]["documentId"]
        read_token = completed["document"]["capabilities"]["readDocument"]
        normal_document = await client.get(
            f"/api/v1/documents/{document_id}",
            headers={"X-Document-Token": read_token},
        )
        assert normal_document.status_code == 200
        assert normal_document.json()["data"]["sourceKind"] == "pdf"
        assert [page["ordinal"] for page in normal_document.json()["data"]["pages"]] == [0, 1]

    assert not settings.pdf_spool_root.joinpath("imports", str(import_id)).exists()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pdf_import_idempotency_replay_and_mismatch_are_explicit(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    app = create_app(settings)
    first_pdf = _pdf_bytes(pages=1)
    different_pdf = _pdf_bytes(pages=2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": "stable-pdf-key"},
            data={"studyLanguage": "pt-BR"},
            files={"pdf": ("first.pdf", first_pdf, "application/pdf")},
        )
        replay = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": "stable-pdf-key"},
            data={"studyLanguage": "pt-BR"},
            files={"pdf": ("renamed.pdf", first_pdf, "application/pdf")},
        )
        mismatch_content = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": "stable-pdf-key"},
            data={"studyLanguage": "pt-BR"},
            files={"pdf": ("different.pdf", different_pdf, "application/pdf")},
        )
        mismatch_language = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": "stable-pdf-key"},
            data={"studyLanguage": "en"},
            files={"pdf": ("first.pdf", first_pdf, "application/pdf")},
        )

    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["data"]["importId"] == first.json()["data"]["importId"]
    assert mismatch_content.status_code == 409
    assert mismatch_content.json()["error"]["code"] == "idempotency_conflict"
    assert mismatch_language.status_code == 409
    assert mismatch_language.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_renderer_import_never_creates_partial_document(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    app = create_app(settings)
    malformed = b"%PDF-1.7\nthis is malformed\n"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": "malformed-pdf"},
            files={"pdf": ("bad.pdf", malformed, "application/pdf")},
        )
        assert accepted.status_code == 202
        admitted = accepted.json()["data"]

        engine, sessions = create_database(settings.require_database_url())
        coordinator = PdfImportCoordinator(
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
            worker_id="pdf-import-failure-test",
        )
        try:
            await _run_actual_render_boundary(settings, coordinator)
            async with sessions() as session:
                assert await session.scalar(select(func.count(DocumentRecord.id))) == 0
                assert await session.scalar(select(func.count(PageRecord.id))) == 0
                assert await session.scalar(select(func.count(JobRecord.id))) == 0
                record = (await session.execute(select(DocumentImportRecord))).scalar_one()
                assert record.status == "failed"
                assert record.error_code == "pdf_invalid"
                assert record.source_cleaned_at is not None
        finally:
            await engine.dispose()

        status = await client.get(
            f"/api/v1/document-imports/{admitted['importId']}",
            headers={
                "X-Document-Import-Token": admitted["capabilities"]["readDocumentImport"]
            },
        )

    assert status.status_code == 200
    assert status.json()["data"]["status"] == "failed"
    assert status.json()["data"]["errorCode"] == "pdf_invalid"
    assert status.json()["data"]["document"] is None
