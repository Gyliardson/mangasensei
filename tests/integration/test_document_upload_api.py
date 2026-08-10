from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.application.document_uploads import DocumentUploadService
from mangasensei.config import Settings
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord

_PEPPER = "integration-test-pepper-value-0001"


def page_image(
    color: tuple[int, int, int] = (240, 235, 225),
    *,
    size: tuple[int, int] = (80, 120),
    compress_level: int = 6,
) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color=color).save(output, format="PNG", compress_level=compress_level)
    return output.getvalue()


def make_settings(database_url: str, storage_root: Path, **updates: object) -> Settings:
    settings = Settings(
        environment="test",
        database_url=database_url,
        storage_root=storage_root,
        model_cache=storage_root / "models",
        capability_peppers=(_PEPPER,),
    )
    return settings.model_copy(update=updates)


def document_files(*items: tuple[str, bytes]) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("images[]", (name, content, "image/png")) for name, content in items]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_create_preserves_order_jobs_language_retention_and_capabilities(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    first = page_image((10, 20, 30))
    second = page_image((200, 180, 160))
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-create-order-0001"},
            data={"studyLanguage": "en"},
            files=document_files(("z-last-lexically.png", first), ("a-first-lexically.png", second)),
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert data["sourceKind"] == "images"
    assert data["orderRevision"] == 1
    assert [page["ordinal"] for page in data["pages"]] == [0, 1]
    assert data["progress"] == {
        "totalPages": 2,
        "completedPages": 0,
        "processingPages": 2,
        "failedPages": 0,
    }
    assert set(data["capabilities"]) == {
        "readDocument",
        "readDocumentImage",
        "reprocessDocument",
    }
    assert all(data["capabilities"].values())

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        document = (await session.execute(select(DocumentRecord))).scalar_one()
        pages = tuple(
            (
                await session.execute(select(PageRecord).order_by(PageRecord.ordinal))
            ).scalars()
        )
        jobs = tuple((await session.execute(select(JobRecord).order_by(JobRecord.page_id))).scalars())
        capability_scopes = set(
            (await session.execute(select(DocumentCapabilityRecord.scope))).scalars()
        )

    assert [page.original_filename for page in pages] == [
        "z-last-lexically.png",
        "a-first-lexically.png",
    ]
    assert [page.ordinal for page in pages] == [0, 1]
    assert all(page.document_id == document.id for page in pages)
    assert all(page.upload_key_id is None for page in pages)
    assert all(page.upload_idempotency_digest is None for page in pages)
    assert all(page.created_at == document.created_at for page in pages)
    assert all(page.expires_at == document.expires_at for page in pages)
    assert len(jobs) == 2
    assert all(job.job_kind == "page_analysis" for job in jobs)
    assert all(job.study_language == "en" for job in jobs)
    assert capability_scopes == {"read:document", "read:document-image", "reprocess:document"}
    assert data["expiresAt"] == document.expires_at.isoformat()
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_duplicate_document_images_create_distinct_pages_sharing_one_blob(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    duplicate = page_image((40, 50, 60))
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-duplicates-0001"},
            files=document_files(("copy-a.png", duplicate), ("copy-b.png", duplicate)),
        )

    assert response.status_code == 202
    data = response.json()["data"]
    assert len(data["pages"]) == 2
    assert data["pages"][0]["pageId"] != data["pages"][1]["pageId"]

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        pages = tuple(
            (await session.execute(select(PageRecord).order_by(PageRecord.ordinal))).scalars()
        )
        blob_count = await session.scalar(select(func.count()).select_from(ImageBlobRecord))
        job_count = await session.scalar(select(func.count()).select_from(JobRecord))
    assert [page.ordinal for page in pages] == [0, 1]
    assert pages[0].image_blob_id == pages[1].image_blob_id
    assert blob_count == 1
    assert job_count == 2
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_creation_replay_reissues_capabilities_without_duplicate_rows(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    first = page_image((12, 34, 56))
    second = page_image((78, 90, 123))
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    request = document_files(("1.png", first), ("2.png", second))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-replay-0001"},
            data={"studyLanguage": "pt-BR"},
            files=request,
        )
        replay = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-replay-0001"},
            data={"studyLanguage": "pt-BR"},
            files=request,
        )

    assert created.status_code == 202
    assert replay.status_code == 200
    created_data = created.json()["data"]
    replay_data = replay.json()["data"]
    assert replay_data["documentId"] == created_data["documentId"]
    assert replay_data["pages"] == created_data["pages"]
    assert replay_data["capabilities"] != created_data["capabilities"]

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DocumentRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(PageRecord)) == 2
        assert await session.scalar(select(func.count()).select_from(JobRecord)) == 2
        assert await session.scalar(select(func.count()).select_from(DocumentCapabilityRecord)) == 6
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["order", "content", "language"])
async def test_document_creation_idempotency_conflicts_when_request_identity_changes(
    clean_postgres_url: str,
    tmp_path: Path,
    mutation: str,
) -> None:
    first = page_image((1, 2, 3))
    second = page_image((4, 5, 6))
    replacement = page_image((7, 8, 9))
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": f"document-conflict-{mutation}"},
            data={"studyLanguage": "en"},
            files=document_files(("first.png", first), ("second.png", second)),
        )
        assert created.status_code == 202
        files = document_files(("first.png", first), ("second.png", second))
        language = "en"
        if mutation == "order":
            files = document_files(("second.png", second), ("first.png", first))
        elif mutation == "content":
            files = document_files(("first.png", first), ("replacement.png", replacement))
        else:
            language = "pt-BR"
        conflict = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": f"document-conflict-{mutation}"},
            data={"studyLanguage": language},
            files=files,
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_upload_rejects_empty_and_unsupported_media_without_partial_document(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    valid = page_image()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-empty-0001"},
            data={"studyLanguage": "en"},
        )
        invalid = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-invalid-0001"},
            files=[
                ("images[]", ("valid.png", valid, "image/png")),
                ("images[]", ("invalid.txt", b"not-an-image", "text/plain")),
            ],
        )

    assert empty.status_code == 422
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_image"
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DocumentRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(PageRecord)) == 0
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_max_page_limit_is_enforced_before_durable_work(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path, max_document_images=1))
    image = page_image()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-page-limit-0001"},
            files=document_files(("1.png", image), ("2.png", image)),
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_page_limit_exceeded"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_aggregate_byte_limit_is_independent_of_per_image_limit(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    large = page_image(size=(1800, 1800), compress_level=0)
    assert len(large) < 12 * 1024 * 1024
    assert len(large) * 2 > 12 * 1024 * 1024
    app = create_app(
        make_settings(clean_postgres_url, tmp_path, max_document_bytes=12 * 1024 * 1024)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-byte-limit-0001"},
            files=document_files(("1.png", large), ("2.png", large)),
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_byte_limit_exceeded"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_aggregate_pixel_limit_is_independent_of_per_image_limit(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    large = page_image(size=(4000, 4000))
    app = create_app(make_settings(clean_postgres_url, tmp_path, max_document_pixels=25_000_000))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-pixel-limit-0001"},
            files=document_files(("1.png", large), ("2.png", large)),
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "document_pixel_limit_exceeded"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_transaction_failure_never_exposes_partial_document(
    clean_postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_capabilities(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("injected document persistence failure")

    monkeypatch.setattr(DocumentUploadService, "_issue_capabilities", fail_capabilities)
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    image = page_image((9, 9, 9))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-transaction-failure-0001"},
            files=document_files(("1.png", image), ("2.png", image)),
        )
    assert response.status_code == 500

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DocumentRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(PageRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(JobRecord)) == 0
    await engine.dispose()
