from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mangasensei.application.authorization import ResourceNotFoundError
from mangasensei.application.document_authorization import DocumentAuthorizer
from mangasensei.application.document_uploads import DocumentUploadService
from mangasensei.domain.languages import StudyLanguage
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.analysis_models import (
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
    LinguisticRunRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
    DocumentRetryRequestRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.operational_models import RateLimitBucketRecord
from mangasensei.infrastructure.database.storage_models import (
    ImageBlobRecord,
    PageCapabilityRecord,
    PageRecord,
)
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.retention import RetentionJanitor
from tests.large_document.generator import PAGE_HEIGHT, PAGE_WIDTH, generate_pages

_TEST_PEPPER = "integration-test-pepper-value-0001"


def _async_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


def _image(seed: bytes) -> ValidatedImage:
    return ValidatedImage(
        content=seed,
        sha256=hashlib.sha256(seed).hexdigest(),
        width=10,
        height=10,
        media_type="image/png",
        format="PNG",
    )


async def _add_blob(
    session: AsyncSession,
    storage: LocalFilesystemStorage,
    *,
    seed: bytes,
) -> ImageBlobRecord:
    image = _image(seed)
    key = await storage.store(image)
    blob = ImageBlobRecord(
        sha256=bytes.fromhex(image.sha256),
        byte_size=len(image.content),
        width=image.width,
        height=image.height,
        media_type=image.media_type,
        storage_key=key,
    )
    session.add(blob)
    await session.flush()
    return blob


class CountingRetentionJanitor(RetentionJanitor):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFilesystemStorage,
    ) -> None:
        super().__init__(sessions, storage)
        self.blob_cleanup_attempts: list[int] = []

    async def _delete_if_unreferenced(self, blob_id: int) -> None:
        self.blob_cleanup_attempts.append(blob_id)
        await super()._delete_if_unreferenced(blob_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_bounds_200_page_document_and_drains_across_cycles(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    capabilities = CapabilityService((_TEST_PEPPER,))
    uploader = DocumentUploadService(
        sessions=sessions,
        storage=storage,
        capability_service=capabilities,
        idempotency_pepper=_TEST_PEPPER,
    )
    generated = generate_pages()
    images = tuple(
        ValidatedImage(
            content=page.content,
            sha256=page.sha256,
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
            media_type="image/png",
            format="PNG",
        )
        for page in generated
    )
    created = await uploader.create(
        images=images,
        original_filenames=tuple(page.filename for page in generated),
        idempotency_key="retention-e2-200-page-document-0001",
        study_language=StudyLanguage.PORTUGUESE_BRAZIL,
    )

    expired_at = datetime.now(UTC) - timedelta(minutes=1)
    expired_created_at = expired_at - timedelta(hours=24)
    live_image = _image(b"retention-e2-live-unrelated")
    live_key = await storage.store(live_image)

    async with sessions.begin() as session:
        document = await session.get_one(DocumentRecord, created.internal_id)
        document.created_at = expired_created_at
        document.expires_at = expired_at
        pages = tuple(
            (
                await session.execute(
                    select(PageRecord)
                    .where(PageRecord.document_id == document.id)
                    .order_by(PageRecord.id)
                )
            ).scalars()
        )
        assert len(pages) == 200
        for page in pages:
            page.created_at = expired_created_at
            page.expires_at = expired_at

        document_page_ids = tuple(page.id for page in pages)
        document_blob_ids = tuple(page.image_blob_id for page in pages)
        jobs = tuple(
            (
                await session.execute(
                    select(JobRecord)
                    .where(JobRecord.page_id.in_(document_page_ids))
                    .order_by(JobRecord.page_id)
                )
            ).scalars()
        )
        assert len(jobs) == 200

        first_job = jobs[0]
        ocr_run = OcrRunRecord(
            job_id=first_job.id,
            fencing_token=first_job.fencing_token,
            detector="retention-test",
            recognizer="retention-test",
            model_manifest_version="retention-test-v1",
            config_digest=hashlib.sha256(b"ocr-config").digest(),
            upstream_repository="retention-test",
            upstream_commit="retention-test",
            input_sha256=pages[0].request_digest,
            width=PAGE_WIDTH,
            height=PAGE_HEIGHT,
        )
        session.add(ocr_run)
        await session.flush()
        linguistic_run = LinguisticRunRecord(
            job_id=first_job.id,
            ocr_run_id=ocr_run.id,
            fencing_token=first_job.fencing_token,
            tokenizer_name="retention-test",
            tokenizer_version="1",
            config_digest=hashlib.sha256(b"linguistic-config").digest(),
            dictionary_name="retention-test",
            dictionary_version="1",
            dictionary_digest=hashlib.sha256(b"dictionary").digest(),
            input_digest=hashlib.sha256(b"linguistic-input").digest(),
        )
        session.add(linguistic_run)
        await session.flush()
        study_result = StudyResultRecord(
            job_id=first_job.id,
            linguistic_run_id=linguistic_run.id,
            content_language="ja",
            study_language="pt-BR",
            dictionary_language="en",
        )
        page_capability = PageCapabilityRecord(
            page_id=pages[0].id,
            key_id="v1",
            scope="read:page",
            digest=hashlib.sha256(b"retention-e2-page-capability").digest(),
            expires_at=created.expires_at,
        )
        retry_request = DocumentRetryRequestRecord(
            document_id=document.id,
            idempotency_digest=hashlib.sha256(b"retention-e2-retry-request").digest(),
        )
        session.add_all((study_result, page_capability, retry_request))
        await session.flush()

        live_blob = ImageBlobRecord(
            sha256=bytes.fromhex(live_image.sha256),
            byte_size=len(live_image.content),
            width=live_image.width,
            height=live_image.height,
            media_type=live_image.media_type,
            storage_key=live_key,
        )
        session.add(live_blob)
        await session.flush()
        live_created_at = datetime.now(UTC)
        live_page = PageRecord(
            image_blob_id=live_blob.id,
            original_filename="live.png",
            upload_key_id="v1",
            upload_idempotency_digest=hashlib.sha256(b"retention-e2-live-key").digest(),
            request_digest=bytes.fromhex(live_image.sha256),
            created_at=live_created_at,
            expires_at=live_created_at + timedelta(hours=24),
        )
        session.add(live_page)
        await session.flush()

        last_page_public_id = pages[-1].public_id
        study_result_id = study_result.id
        ocr_run_id = ocr_run.id
        linguistic_run_id = linguistic_run.id
        page_capability_id = page_capability.id
        retry_request_id = retry_request.id
        live_page_id = live_page.id
        live_blob_id = live_blob.id

    janitor = CountingRetentionJanitor(sessions, storage)
    batch_size = 25

    attempts_before = len(janitor.blob_cleanup_attempts)
    assert await janitor.run_once(batch_size=batch_size) == batch_size
    assert len(janitor.blob_cleanup_attempts) - attempts_before == batch_size

    async with sessions() as session:
        remaining_document_pages = tuple(
            (
                await session.execute(
                    select(PageRecord.id).where(PageRecord.document_id == created.internal_id)
                )
            ).scalars()
        )
        assert len(remaining_document_pages) == 175
        assert await session.get(DocumentRecord, created.internal_id) is not None
        remaining_document_blobs = tuple(
            (
                await session.execute(
                    select(ImageBlobRecord.id).where(ImageBlobRecord.id.in_(document_blob_ids))
                )
            ).scalars()
        )
        assert len(remaining_document_blobs) == 175
        remaining_document_jobs = tuple(
            (
                await session.execute(
                    select(JobRecord.id).where(JobRecord.page_id.in_(remaining_document_pages))
                )
            ).scalars()
        )
        assert len(remaining_document_jobs) == 175
        assert await session.get(StudyResultRecord, study_result_id) is None
        assert await session.get(OcrRunRecord, ocr_run_id) is None
        assert await session.get(LinguisticRunRecord, linguistic_run_id) is None
        assert await session.get(PageCapabilityRecord, page_capability_id) is None
        assert await session.get(DocumentRetryRequestRecord, retry_request_id) is not None
        document_capabilities = tuple(
            (
                await session.execute(
                    select(DocumentCapabilityRecord.id).where(
                        DocumentCapabilityRecord.document_id == created.internal_id
                    )
                )
            ).scalars()
        )
        assert len(document_capabilities) == 4
        assert await session.get(PageRecord, live_page_id) is not None
        assert await session.get(ImageBlobRecord, live_blob_id) is not None

    authorizer = DocumentAuthorizer(sessions, capabilities)
    with pytest.raises(ResourceNotFoundError):
        await authorizer.authorize_document(
            document_id=created.document_id,
            token=created.capabilities.read_document,
        )
    with pytest.raises(ResourceNotFoundError):
        await authorizer.authorize_image(
            document_id=created.document_id,
            page_id=last_page_public_id,
            token=created.capabilities.read_document_image,
        )

    for expected_remaining in (150, 125, 100, 75, 50, 25, 0):
        attempts_before = len(janitor.blob_cleanup_attempts)
        assert await janitor.run_once(batch_size=batch_size) == batch_size
        assert len(janitor.blob_cleanup_attempts) - attempts_before == batch_size
        async with sessions() as session:
            remaining = tuple(
                (
                    await session.execute(
                        select(PageRecord.id).where(PageRecord.document_id == created.internal_id)
                    )
                ).scalars()
            )
            assert len(remaining) == expected_remaining
            if expected_remaining:
                assert await session.get(DocumentRecord, created.internal_id) is not None
            else:
                assert await session.get(DocumentRecord, created.internal_id) is None

    async with sessions() as session:
        assert (
            await session.execute(
                select(ImageBlobRecord.id).where(ImageBlobRecord.id.in_(document_blob_ids))
            )
        ).scalars().all() == []
        assert (
            await session.execute(
                select(JobRecord.id).where(JobRecord.page_id.in_(document_page_ids))
            )
        ).scalars().all() == []
        assert await session.get(DocumentRetryRequestRecord, retry_request_id) is None
        assert (
            await session.execute(
                select(DocumentCapabilityRecord.id).where(
                    DocumentCapabilityRecord.document_id == created.internal_id
                )
            )
        ).scalars().all() == []
        assert await session.get(PageRecord, live_page_id) is not None
        assert await session.get(ImageBlobRecord, live_blob_id) is not None
    assert await storage.read(live_key) == live_image.content
    assert len(janitor.blob_cleanup_attempts) == 200
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_blob_cleanup_budget_prioritizes_new_orphans_and_drains_backlog(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    expired_created_at = datetime.now(UTC) - timedelta(hours=25)

    async with sessions.begin() as session:
        old_orphans: list[ImageBlobRecord] = []
        for index in range(5):
            old_orphans.append(
                await _add_blob(session, storage, seed=f"old-orphan-{index}".encode())
            )
        selected_blobs: list[ImageBlobRecord] = []
        for index in range(3):
            blob = await _add_blob(session, storage, seed=f"selected-page-{index}".encode())
            selected_blobs.append(blob)
            session.add(
                PageRecord(
                    image_blob_id=blob.id,
                    original_filename=f"expired-{index}.png",
                    upload_key_id="v1",
                    upload_idempotency_digest=hashlib.sha256(
                        f"expired-key-{index}".encode()
                    ).digest(),
                    request_digest=blob.sha256,
                    created_at=expired_created_at,
                    expires_at=expired_created_at + timedelta(hours=24),
                )
            )
        old_orphan_ids = tuple(blob.id for blob in old_orphans)
        selected_blob_ids = tuple(blob.id for blob in selected_blobs)

    janitor = CountingRetentionJanitor(sessions, storage)
    assert await janitor.run_once(batch_size=3) == 3
    assert tuple(janitor.blob_cleanup_attempts) == selected_blob_ids

    async with sessions() as session:
        remaining_old = tuple(
            (
                await session.execute(
                    select(ImageBlobRecord.id).where(ImageBlobRecord.id.in_(old_orphan_ids))
                )
            ).scalars()
        )
        assert set(remaining_old) == set(old_orphan_ids)

    attempts_before = len(janitor.blob_cleanup_attempts)
    assert await janitor.run_once(batch_size=3) == 0
    assert len(janitor.blob_cleanup_attempts) - attempts_before == 3
    attempts_before = len(janitor.blob_cleanup_attempts)
    assert await janitor.run_once(batch_size=3) == 0
    assert len(janitor.blob_cleanup_attempts) - attempts_before == 2

    async with sessions() as session:
        assert (await session.execute(select(ImageBlobRecord))).scalars().all() == []
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_bounds_stale_rate_limit_bucket_cleanup(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    stale_rows: list[RateLimitBucketRecord] = []
    async with sessions.begin() as session:
        for index in range(5):
            row = RateLimitBucketRecord(
                key_digest=hashlib.sha256(f"stale-bucket-{index}".encode()).digest(),
                action="retention-test",
                window_start=now - timedelta(days=2, minutes=index),
                request_count=1,
            )
            stale_rows.append(row)
            session.add(row)
        current = RateLimitBucketRecord(
            key_digest=hashlib.sha256(b"current-bucket").digest(),
            action="retention-test",
            window_start=now,
            request_count=1,
        )
        session.add(current)
        current_key = (current.key_digest, current.action, current.window_start)

    janitor = RetentionJanitor(sessions, LocalFilesystemStorage(tmp_path))
    assert await janitor.run_once(batch_size=2) == 0

    oldest_keys = {
        (row.key_digest, row.action, row.window_start) for row in stale_rows[-2:]
    }
    async with sessions() as session:
        rows = tuple((await session.execute(select(RateLimitBucketRecord))).scalars())
        remaining_keys = {(row.key_digest, row.action, row.window_start) for row in rows}
        assert len(rows) == 4
        assert current_key in remaining_keys
        assert oldest_keys.isdisjoint(remaining_keys)

    assert await janitor.run_once(batch_size=2) == 0
    async with sessions() as session:
        rows = tuple((await session.execute(select(RateLimitBucketRecord))).scalars())
        assert len(rows) == 2
        assert any(
            (row.key_digest, row.action, row.window_start) == current_key for row in rows
        )

    assert await janitor.run_once(batch_size=2) == 0
    async with sessions() as session:
        rows = tuple((await session.execute(select(RateLimitBucketRecord))).scalars())
        assert len(rows) == 1
        assert (rows[0].key_digest, rows[0].action, rows[0].window_start) == current_key
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_expiry_controls_bounded_page_and_gemini_reconciliation(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    document_expired_at = datetime.now(UTC) - timedelta(minutes=1)
    document_created_at = document_expired_at - timedelta(hours=24)
    child_created_at = datetime.now(UTC)
    reservation = Decimal("0.100000")

    async with sessions.begin() as session:
        document = DocumentRecord(
            created_at=document_created_at,
            expires_at=document_expired_at,
        )
        session.add(document)
        await session.flush()
        pages: list[PageRecord] = []
        calls: list[GeminiCallRecord] = []
        for index in range(3):
            blob = await _add_blob(session, storage, seed=f"gemini-document-{index}".encode())
            page = PageRecord(
                image_blob_id=blob.id,
                document_id=document.id,
                ordinal=index,
                original_filename=f"page-{index}.png",
                upload_key_id=None,
                upload_idempotency_digest=None,
                request_digest=blob.sha256,
                created_at=child_created_at,
                expires_at=child_created_at + timedelta(hours=24),
            )
            session.add(page)
            await session.flush()
            job = JobRecord(
                page_id=page.id,
                idempotency_digest=hashlib.sha256(f"gemini-job-{index}".encode()).digest(),
                request_digest=page.request_digest,
            )
            session.add(job)
            await session.flush()
            call = GeminiCallRecord(
                page_id=page.id,
                job_id=job.id,
                page_call_ordinal=1,
                fencing_token=0,
                model="retention-test",
                prompt_version="retention-test-v1",
                schema_version="v1",
                request_digest=hashlib.sha256(f"gemini-request-{index}".encode()).digest(),
                reserved_cost=reservation,
                state="reserved",
                created_at=child_created_at,
            )
            session.add(call)
            pages.append(page)
            calls.append(call)
        session.add(
            GeminiBudgetBucketRecord(
                budget_date=child_created_at.date(),
                currency="USD",
                limit_amount=Decimal("5.000000"),
                reserved_amount=reservation * 3,
                actual_amount=Decimal("0"),
            )
        )
        await session.flush()
        document_id = document.id
        page_ids = tuple(page.id for page in pages)
        call_ids = tuple(call.id for call in calls)

    janitor = RetentionJanitor(sessions, storage)
    assert await janitor.run_once(batch_size=2) == 2

    async with sessions() as session:
        remaining_pages = tuple(
            (
                await session.execute(
                    select(PageRecord.id)
                    .where(PageRecord.document_id == document_id)
                    .order_by(PageRecord.id)
                )
            ).scalars()
        )
        assert remaining_pages == (page_ids[-1],)
        assert await session.get(DocumentRecord, document_id) is not None
        reconciled_calls = tuple(
            (
                await session.execute(
                    select(GeminiCallRecord)
                    .where(GeminiCallRecord.id.in_(call_ids))
                    .order_by(GeminiCallRecord.id)
                )
            ).scalars()
        )
        assert [call.state for call in reconciled_calls] == ["failed", "failed", "reserved"]
        assert [call.page_id for call in reconciled_calls] == [None, None, page_ids[-1]]
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()
        assert bucket.reserved_amount == reservation

    assert await janitor.run_once(batch_size=2) == 1
    async with sessions() as session:
        assert await session.get(DocumentRecord, document_id) is None
        calls_after = tuple(
            (
                await session.execute(
                    select(GeminiCallRecord)
                    .where(GeminiCallRecord.id.in_(call_ids))
                    .order_by(GeminiCallRecord.id)
                )
            ).scalars()
        )
        assert [call.state for call in calls_after] == ["failed", "failed", "failed"]
        assert all(call.page_id is None for call in calls_after)
        bucket = (await session.execute(select(GeminiBudgetBucketRecord))).scalar_one()
        assert bucket.reserved_amount == Decimal("0")
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_janitors_process_disjoint_bounded_page_batches(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    engine = create_async_engine(_async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    expired_created_at = datetime.now(UTC) - timedelta(hours=25)

    async with sessions.begin() as session:
        for index in range(10):
            blob = await _add_blob(session, storage, seed=f"concurrent-page-{index}".encode())
            session.add(
                PageRecord(
                    image_blob_id=blob.id,
                    original_filename=f"concurrent-{index}.png",
                    upload_key_id="v1",
                    upload_idempotency_digest=hashlib.sha256(
                        f"concurrent-key-{index}".encode()
                    ).digest(),
                    request_digest=blob.sha256,
                    created_at=expired_created_at,
                    expires_at=expired_created_at + timedelta(hours=24),
                )
            )

    first = CountingRetentionJanitor(sessions, storage)
    second = CountingRetentionJanitor(sessions, storage)
    deleted = await asyncio.gather(
        first.run_once(batch_size=3),
        second.run_once(batch_size=3),
    )

    assert deleted == [3, 3]
    assert len(first.blob_cleanup_attempts) <= 3
    assert len(second.blob_cleanup_attempts) <= 3
    assert set(first.blob_cleanup_attempts).isdisjoint(second.blob_cleanup_attempts)
    async with sessions() as session:
        remaining = tuple((await session.execute(select(PageRecord.id))).scalars())
        assert len(remaining) == 4
    await engine.dispose()
