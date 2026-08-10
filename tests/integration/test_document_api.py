from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.application.document_queries import DocumentQueryService
from mangasensei.config import Settings
from mangasensei.domain.capabilities import DocumentCapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.analysis_models import LinguisticRunRecord, OcrRunRecord
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage

_PEPPER = "integration-test-pepper-value-0001"


@dataclass(frozen=True, slots=True)
class SeededDocument:
    document_id: UUID
    page_id: UUID
    read_token: str
    image_token: str
    read_capability_id: int
    image_capability_id: int


def make_settings(database_url: str, storage_root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=storage_root,
        model_cache=storage_root / "models",
        capability_peppers=(_PEPPER,),
    )


def _validated(content: bytes) -> ValidatedImage:
    return ValidatedImage(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        width=10,
        height=10,
        media_type="image/png",
        format="PNG",
    )


async def _seed_readable_document(
    database_url: str,
    storage_root: Path,
    *,
    suffix: str,
) -> SeededDocument:
    async_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(storage_root)
    image = _validated(f"document-image-{suffix}".encode())
    storage_key = await storage.store(image)
    capability_service = CapabilityService((_PEPPER,))

    async with sessions.begin() as session:
        document = DocumentRecord()
        session.add(document)
        await session.flush()
        blob = ImageBlobRecord(
            sha256=bytes.fromhex(image.sha256),
            byte_size=len(image.content),
            width=image.width,
            height=image.height,
            media_type=image.media_type,
            storage_key=storage_key,
        )
        session.add(blob)
        await session.flush()
        page = PageRecord(
            image_blob_id=blob.id,
            document_id=document.id,
            ordinal=0,
            original_filename=f"{suffix}.png",
            upload_key_id=None,
            upload_idempotency_digest=None,
            request_digest=bytes.fromhex(image.sha256),
        )
        session.add(page)
        await session.flush()
        session.add(
            JobRecord(
                page_id=page.id,
                idempotency_digest=hashlib.sha256(f"job-{suffix}".encode()).digest(),
                request_digest=bytes.fromhex(image.sha256),
            )
        )
        read = capability_service.issue(
            resource_id=str(document.public_id),
            scope=DocumentCapabilityScope.READ_DOCUMENT,
            expires_at=document.expires_at,
        )
        image_cap = capability_service.issue(
            resource_id=str(document.public_id),
            scope=DocumentCapabilityScope.READ_DOCUMENT_IMAGE,
            expires_at=document.expires_at,
        )
        read_record = DocumentCapabilityRecord(
            document_id=document.id,
            key_id="v1",
            scope=DocumentCapabilityScope.READ_DOCUMENT.value,
            digest=bytes.fromhex(read.persisted_digest),
            expires_at=read.expires_at,
        )
        image_record = DocumentCapabilityRecord(
            document_id=document.id,
            key_id="v1",
            scope=DocumentCapabilityScope.READ_DOCUMENT_IMAGE.value,
            digest=bytes.fromhex(image_cap.persisted_digest),
            expires_at=image_cap.expires_at,
        )
        session.add_all((read_record, image_record))
        await session.flush()
        seeded = SeededDocument(
            document_id=document.public_id,
            page_id=page.public_id,
            read_token=read.token,
            image_token=image_cap.token,
            read_capability_id=read_record.id,
            image_capability_id=image_record.id,
        )

    await engine.dispose()
    return seeded


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_reads_are_capability_scoped_and_membership_protected(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    first = await _seed_readable_document(clean_postgres_url, tmp_path, suffix="first")
    second = await _seed_readable_document(clean_postgres_url, tmp_path, suffix="second")
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document = await client.get(
            f"/api/v1/documents/{first.document_id}",
            headers={"X-Document-Token": first.read_token},
        )
        progress = await client.get(
            f"/api/v1/documents/{first.document_id}/progress",
            headers={"X-Document-Token": first.read_token},
        )
        page = await client.get(
            f"/api/v1/documents/{first.document_id}/pages/{first.page_id}",
            headers={"X-Document-Token": first.read_token},
        )
        image = await client.get(
            f"/api/v1/documents/{first.document_id}/pages/{first.page_id}/image",
            headers={"X-Document-Token": first.image_token},
        )
        wrong_scope_document = await client.get(
            f"/api/v1/documents/{first.document_id}",
            headers={"X-Document-Token": first.image_token},
        )
        wrong_scope_image = await client.get(
            f"/api/v1/documents/{first.document_id}/pages/{first.page_id}/image",
            headers={"X-Document-Token": first.read_token},
        )
        wrong_document = await client.get(
            f"/api/v1/documents/{second.document_id}",
            headers={"X-Document-Token": first.read_token},
        )
        nonmember = await client.get(
            f"/api/v1/documents/{first.document_id}/pages/{second.page_id}",
            headers={"X-Document-Token": first.read_token},
        )

    assert document.status_code == 200
    assert document.json()["data"]["documentId"] == str(first.document_id)
    assert document.json()["data"]["pages"] == [
        {
            "pageId": str(first.page_id),
            "ordinal": 0,
            "status": "pending",
            "resultAvailable": False,
        }
    ]
    assert document.json()["data"]["progress"] == {
        "totalPages": 1,
        "completedPages": 0,
        "processingPages": 1,
        "failedPages": 0,
    }
    assert progress.status_code == 200
    assert progress.json()["data"] == document.json()["data"]["progress"]
    assert page.status_code == 200
    assert page.json()["data"]["pageId"] == str(first.page_id)
    assert page.json()["data"]["status"] == "pending"
    assert page.json()["data"]["imageUrl"].endswith(f"/{first.page_id}/image")
    assert image.status_code == 200
    assert image.content == b"document-image-first"
    assert image.headers["cache-control"] == "private, no-store"
    assert wrong_scope_document.status_code == 404
    assert wrong_scope_image.status_code == 404
    assert wrong_document.status_code == 404
    assert nonmember.status_code == 404


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_capability_revocation_and_expiry_are_uniform_not_found(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    seeded = await _seed_readable_document(clean_postgres_url, tmp_path, suffix="auth-state")
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with sessions.begin() as session:
        read_cap = await session.get_one(DocumentCapabilityRecord, seeded.read_capability_id)
        read_cap.revoked_at = datetime.now(UTC)
        image_cap = await session.get_one(DocumentCapabilityRecord, seeded.image_capability_id)
        image_cap.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        revoked = await client.get(
            f"/api/v1/documents/{seeded.document_id}",
            headers={"X-Document-Token": seeded.read_token},
        )
        expired = await client.get(
            f"/api/v1/documents/{seeded.document_id}/pages/{seeded.page_id}/image",
            headers={"X-Document-Token": seeded.image_token},
        )

    assert revoked.status_code == 404
    assert revoked.json()["error"]["code"] == "not_found"
    assert expired.status_code == 404
    assert expired.json()["error"]["code"] == "not_found"
    await engine.dispose()


async def _add_completed_result(
    session: AsyncSession,
    *,
    job: JobRecord,
    digest: bytes,
) -> None:
    ocr = OcrRunRecord(
        job_id=job.id,
        fencing_token=0,
        detector="test-detector",
        recognizer="test-recognizer",
        model_manifest_version="test",
        config_digest=digest,
        upstream_repository="https://example.invalid/test",
        upstream_commit="test",
        input_sha256=digest,
        width=10,
        height=10,
    )
    session.add(ocr)
    await session.flush()
    linguistic = LinguisticRunRecord(
        job_id=job.id,
        ocr_run_id=ocr.id,
        fencing_token=0,
        tokenizer_name="test-tokenizer",
        tokenizer_version="test",
        config_digest=digest,
        dictionary_name="JMdict",
        dictionary_version="test",
        dictionary_digest=digest,
        input_digest=digest,
    )
    session.add(linguistic)
    await session.flush()
    session.add(
        StudyResultRecord(
            job_id=job.id,
            linguistic_run_id=linguistic.id,
            content_language="ja",
            study_language="pt-BR",
            dictionary_language="en",
        )
    )
    await session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_progress_partitions_pages_without_double_counting_readable_reprocesses(
    clean_postgres_url: str,
) -> None:
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    digest = hashlib.sha256(b"document-progress").digest()

    async with sessions.begin() as session:
        document = DocumentRecord()
        blob = ImageBlobRecord(
            sha256=digest,
            byte_size=100,
            width=10,
            height=10,
            media_type="image/png",
            storage_key=f"objects/{digest.hex()}",
        )
        session.add_all((document, blob))
        await session.flush()

        pages: list[PageRecord] = []
        for ordinal in range(4):
            page = PageRecord(
                image_blob_id=blob.id,
                document_id=document.id,
                ordinal=ordinal,
                original_filename=f"{ordinal}.png",
                upload_key_id=None,
                upload_idempotency_digest=None,
                request_digest=digest,
            )
            session.add(page)
            pages.append(page)
        await session.flush()

        pending = JobRecord(
            page_id=pages[0].id,
            idempotency_digest=hashlib.sha256(b"pending").digest(),
            request_digest=digest,
        )
        failed = JobRecord(
            page_id=pages[1].id,
            idempotency_digest=hashlib.sha256(b"failed").digest(),
            request_digest=digest,
            status="failed",
            finished_at=datetime.now(UTC),
        )
        completed_then_failed = JobRecord(
            page_id=pages[2].id,
            idempotency_digest=hashlib.sha256(b"completed-failed-old").digest(),
            request_digest=digest,
            status="completed",
            finished_at=datetime.now(UTC),
        )
        completed_then_pending = JobRecord(
            page_id=pages[3].id,
            idempotency_digest=hashlib.sha256(b"completed-pending-old").digest(),
            request_digest=digest,
            status="completed",
            finished_at=datetime.now(UTC),
        )
        session.add_all((pending, failed, completed_then_failed, completed_then_pending))
        await session.flush()
        await _add_completed_result(session, job=completed_then_failed, digest=digest)
        await _add_completed_result(session, job=completed_then_pending, digest=digest)
        session.add_all(
            (
                JobRecord(
                    page_id=pages[2].id,
                    job_kind="study_language_reprocess",
                    idempotency_digest=hashlib.sha256(b"completed-failed-new").digest(),
                    request_digest=digest,
                    status="failed",
                    finished_at=datetime.now(UTC),
                ),
                JobRecord(
                    page_id=pages[3].id,
                    job_kind="study_language_reprocess",
                    idempotency_digest=hashlib.sha256(b"completed-pending-new").digest(),
                    request_digest=digest,
                ),
            )
        )
        await session.flush()
        document_id = document.id

    projection = await DocumentQueryService(sessions).get(document_id)

    assert projection["progress"] == {
        "totalPages": 4,
        "completedPages": 2,
        "processingPages": 1,
        "failedPages": 1,
    }
    progress = projection["progress"]
    assert (
        progress["completedPages"] + progress["processingPages"] + progress["failedPages"]
        == progress["totalPages"]
    )
    assert [page["ordinal"] for page in projection["pages"]] == [0, 1, 2, 3]
    assert projection["pages"][2]["status"] == "failed"
    assert projection["pages"][2]["resultAvailable"] is True
    assert projection["pages"][3]["status"] == "pending"
    assert projection["pages"][3]["resultAvailable"] is True
    await engine.dispose()
