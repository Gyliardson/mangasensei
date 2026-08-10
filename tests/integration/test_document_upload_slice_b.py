from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.application.document_uploads import DocumentUploadService
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.retention import RetentionJanitor
from tests.integration.job_fixture_helpers import (
    advance_pending_job_to_processing_linguistics,
    finish_processing_job,
)
from tests.integration.test_upload_api import make_settings, page_image


def _document_files(
    pages: list[tuple[str, bytes]],
) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("images[]", (name, content, "image/png")) for name, content in pages]


async def _post_document(
    client: AsyncClient,
    *,
    pages: list[tuple[str, bytes]],
    key: str,
    study_language: str = "pt-BR",
):
    return await client.post(
        "/api/v1/documents",
        headers={"Idempotency-Key": key},
        data={"studyLanguage": study_language},
        files=_document_files(pages),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_create_preserves_multipart_order_jobs_language_expiry_and_duplicate_blob(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    repeated = page_image((10, 20, 30))
    other = page_image((40, 50, 60))
    pages = [
        ("z-first.png", repeated),
        ("a-second.png", other),
        ("m-third-duplicate.png", repeated),
    ]
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _post_document(
            client,
            pages=pages,
            key="document-create-order-0001",
            study_language="en",
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["sourceKind"] == "images"
    assert data["orderRevision"] == 1
    assert [page["ordinal"] for page in data["pages"]] == [0, 1, 2]
    assert data["progress"] == {
        "totalPages": 3,
        "completedPages": 0,
        "processingPages": 3,
        "failedPages": 0,
    }
    assert set(data["capabilities"]) == {
        "readDocument",
        "readDocumentImage",
        "reprocessDocument",
    }

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        document = (await session.execute(select(DocumentRecord))).scalar_one()
        stored_pages = (
            await session.execute(select(PageRecord).order_by(PageRecord.ordinal))
        ).scalars().all()
        jobs = (
            await session.execute(select(JobRecord).order_by(JobRecord.page_id))
        ).scalars().all()
        blobs = (await session.execute(select(ImageBlobRecord))).scalars().all()
        capabilities = (
            await session.execute(select(DocumentCapabilityRecord))
        ).scalars().all()

    assert [page.original_filename for page in stored_pages] == [name for name, _ in pages]
    assert [page.ordinal for page in stored_pages] == [0, 1, 2]
    assert all(page.document_id == document.id for page in stored_pages)
    assert all(page.upload_key_id is None for page in stored_pages)
    assert all(page.upload_idempotency_digest is None for page in stored_pages)
    assert all(page.created_at == document.created_at for page in stored_pages)
    assert all(page.expires_at == document.expires_at for page in stored_pages)
    assert len(jobs) == 3
    assert all(job.job_kind == "page_analysis" for job in jobs)
    assert all(job.study_language == "en" for job in jobs)
    assert len(blobs) == 2
    assert stored_pages[0].image_blob_id == stored_pages[2].image_blob_id
    assert stored_pages[0].image_blob_id != stored_pages[1].image_blob_id
    assert {capability.scope for capability in capabilities} == {
        "read:document",
        "read:document-image",
        "reprocess:document",
    }
    assert all(capability.expires_at == document.expires_at for capability in capabilities)
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_create_replay_reuses_logical_document_without_duplicate_rows(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    pages = [
        ("two.png", page_image((90, 80, 70))),
        ("one.png", page_image((60, 50, 40))),
    ]
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await _post_document(client, pages=pages, key="document-replay-same-0001")
        replay = await _post_document(client, pages=pages, key="document-replay-same-0001")

    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["data"]["documentId"] == first.json()["data"]["documentId"]
    assert (
        replay.json()["data"]["capabilities"]["readDocument"]
        != first.json()["data"]["capabilities"]["readDocument"]
    )

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine)
    async with sessions() as session:
        assert len((await session.execute(select(DocumentRecord))).scalars().all()) == 1
        assert len((await session.execute(select(PageRecord))).scalars().all()) == 2
        assert len((await session.execute(select(JobRecord))).scalars().all()) == 2
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["order", "content", "language", "count"])
async def test_document_create_idempotency_conflicts_on_request_identity_change(
    clean_postgres_url: str,
    tmp_path: Path,
    change: str,
) -> None:
    first_image = page_image((100, 10, 10))
    second_image = page_image((10, 100, 10))
    base = [("first.png", first_image), ("second.png", second_image)]
    changed_pages = base
    changed_language = "pt-BR"
    if change == "order":
        changed_pages = list(reversed(base))
    elif change == "content":
        changed_pages = [("first.png", page_image((10, 10, 100))), base[1]]
    elif change == "language":
        changed_language = "en"
    elif change == "count":
        changed_pages = base[:1]

    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await _post_document(client, pages=base, key="document-conflict-identity-0001")
        conflict = await _post_document(
            client,
            pages=changed_pages,
            key="document-conflict-identity-0001",
            study_language=changed_language,
        )

    assert created.status_code == 202
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_create_enforces_page_limit_before_persistence(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = make_settings(clean_postgres_url, tmp_path).model_copy(
        update={"max_document_images": 2}
    )
    app = create_app(settings)
    pages = [
        ("1.png", page_image((1, 2, 3))),
        ("2.png", page_image((4, 5, 6))),
        ("3.png", page_image((7, 8, 9))),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await _post_document(client, pages=pages, key="document-page-limit-0001")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_page_limit_exceeded"
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine)
    async with sessions() as session:
        assert (await session.execute(select(DocumentRecord))).scalars().all() == []
        assert (await session.execute(select(PageRecord))).scalars().all() == []
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_invalid_image_does_not_expose_partial_document(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-invalid-image-0001"},
            data={"studyLanguage": "pt-BR"},
            files=[
                ("images[]", ("valid.png", page_image(), "image/png")),
                ("images[]", ("invalid.txt", b"not-an-image", "text/plain")),
            ],
        )

    assert response.status_code == 422
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine)
    async with sessions() as session:
        assert (await session.execute(select(DocumentRecord))).scalars().all() == []
        assert (await session.execute(select(PageRecord))).scalars().all() == []
        assert (await session.execute(select(JobRecord))).scalars().all() == []
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_transaction_failure_leaves_only_reconcilable_staged_blob(
    clean_postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_capabilities(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("injected document capability failure")

    monkeypatch.setattr(DocumentUploadService, "_issue_capabilities", fail_capabilities)
    original = page_image((25, 35, 45))
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        failed = await _post_document(
            client,
            pages=[("one.png", original), ("two.png", original)],
            key="document-transaction-failure-0001",
        )

    assert failed.status_code == 500
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        assert (await session.execute(select(DocumentRecord))).scalars().all() == []
        assert (await session.execute(select(PageRecord))).scalars().all() == []
        assert (await session.execute(select(JobRecord))).scalars().all() == []

    storage = LocalFilesystemStorage(tmp_path)
    assert len(await storage.pending_writes()) >= 1
    await RetentionJanitor(sessions, storage).run_once(batch_size=10)
    assert await storage.pending_writes() == ()
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nested_document_reprocess_requires_reprocess_scope_and_membership(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await _post_document(
            client,
            pages=[("a.png", page_image((10, 10, 10))), ("b.png", page_image((20, 20, 20)))],
            key="document-reprocess-first-0001",
        )
        second = await _post_document(
            client,
            pages=[("c.png", page_image((30, 30, 30))), ("d.png", page_image((40, 40, 40)))],
            key="document-reprocess-second-0001",
        )

    first_data = first.json()["data"]
    second_data = second.json()["data"]
    page_id = first_data["pages"][0]["pageId"]
    nonmember_page_id = second_data["pages"][0]["pageId"]

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        jobs = (await session.execute(select(JobRecord))).scalars().all()
        for job in jobs:
            await advance_pending_job_to_processing_linguistics(session, job)
            await finish_processing_job(session, job)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.post(
            f"/api/v1/documents/{first_data['documentId']}/pages/{page_id}/reprocess",
            headers={
                "X-Document-Token": first_data["capabilities"]["reprocessDocument"],
                "Idempotency-Key": "document-child-reprocess-0001",
            },
            json={"studyLanguage": "en"},
        )
        denied_read_scope = await client.post(
            f"/api/v1/documents/{first_data['documentId']}/pages/{page_id}/reprocess",
            headers={
                "X-Document-Token": first_data["capabilities"]["readDocument"],
                "Idempotency-Key": "document-child-reprocess-denied-0001",
            },
            json={"studyLanguage": "en"},
        )
        denied_nonmember = await client.post(
            f"/api/v1/documents/{first_data['documentId']}/pages/{nonmember_page_id}/reprocess",
            headers={
                "X-Document-Token": first_data["capabilities"]["reprocessDocument"],
                "Idempotency-Key": "document-child-reprocess-nonmember-0001",
            },
            json={"studyLanguage": "en"},
        )
        denied_wrong_document = await client.post(
            f"/api/v1/documents/{second_data['documentId']}/pages/{nonmember_page_id}/reprocess",
            headers={
                "X-Document-Token": first_data["capabilities"]["reprocessDocument"],
                "Idempotency-Key": "document-child-reprocess-wrong-document-0001",
            },
            json={"studyLanguage": "en"},
        )

    assert allowed.status_code == 202
    assert allowed.json()["data"]["studyLanguage"] == "en"
    assert denied_read_scope.status_code == 404
    assert denied_nonmember.status_code == 404
    assert denied_wrong_document.status_code == 404
    await engine.dispose()
