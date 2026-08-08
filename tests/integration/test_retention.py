from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.application.uploads import UploadService
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage, PendingStorageWrite
from mangasensei.workers.retention import RetentionJanitor


def validated_image(content: bytes = b"retention-original") -> ValidatedImage:
    return ValidatedImage(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        width=10,
        height=10,
        media_type="image/png",
        format="PNG",
    )


class BlockingDeleteStorage(LocalFilesystemStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.delete_started = asyncio.Event()
        self.allow_delete = asyncio.Event()
        self.stage_started = asyncio.Event()

    async def delete(self, key: str) -> None:
        self.delete_started.set()
        await self.allow_delete.wait()
        await super().delete(key)

    async def stage(self, image: ValidatedImage) -> PendingStorageWrite:
        self.stage_started.set()
        return await super().stage(image)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_deletes_expired_page_and_unreferenced_original(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    database_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    image = validated_image()
    key = await storage.store(image)
    created_at = datetime.now(UTC) - timedelta(hours=25)
    async with sessions.begin() as session:
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
        session.add(
            PageRecord(
                image_blob_id=blob.id,
                original_filename="expired.png",
                upload_key_id="v1",
                upload_idempotency_digest=hashlib.sha256(b"expired-key").digest(),
                request_digest=bytes.fromhex(image.sha256),
                created_at=created_at,
                expires_at=created_at + timedelta(hours=24),
            )
        )

    deleted = await RetentionJanitor(sessions, storage).run_once(batch_size=10)

    assert deleted == 1
    async with sessions() as session:
        assert (await session.execute(select(PageRecord))).scalars().all() == []
        assert (await session.execute(select(ImageBlobRecord))).scalars().all() == []
    with pytest.raises(FileNotFoundError):
        await storage.read(key)
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_retries_blob_cleanup_left_in_deleting_state(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    database_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    image = validated_image(b"interrupted-retention")
    key = await storage.store(image)
    async with sessions.begin() as session:
        session.add(
            ImageBlobRecord(
                sha256=bytes.fromhex(image.sha256),
                byte_size=len(image.content),
                width=image.width,
                height=image.height,
                media_type=image.media_type,
                storage_key=key,
                state="deleting",
            )
        )
    await storage.delete(key)

    deleted = await RetentionJanitor(sessions, storage).run_once(batch_size=10)

    assert deleted == 0
    async with sessions() as session:
        assert (await session.execute(select(ImageBlobRecord))).scalars().all() == []
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_serializes_same_digest_upload_before_filesystem_delete(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    database_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = BlockingDeleteStorage(tmp_path)
    image = validated_image(b"retention-upload-race")
    key = await storage.store(image)
    created_at = datetime.now(UTC) - timedelta(hours=25)
    async with sessions.begin() as session:
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
        session.add(
            PageRecord(
                image_blob_id=blob.id,
                original_filename="expired-race.png",
                upload_key_id="v1",
                upload_idempotency_digest=hashlib.sha256(b"expired-race-key").digest(),
                request_digest=bytes.fromhex(image.sha256),
                created_at=created_at,
                expires_at=created_at + timedelta(hours=24),
            )
        )

    janitor = RetentionJanitor(sessions, storage)
    upload = UploadService(
        sessions=sessions,
        storage=storage,
        capability_service=CapabilityService(("integration-test-pepper-value-0001",)),
        idempotency_pepper="integration-test-pepper-value-0001",
    )
    retention_task = asyncio.create_task(janitor.run_once(batch_size=10))
    await asyncio.wait_for(storage.delete_started.wait(), timeout=2)
    upload_task = asyncio.create_task(
        upload.create(
            image=image,
            original_filename="replacement.png",
            idempotency_key="retention-race-upload-0001",
        )
    )

    try:
        await asyncio.wait_for(storage.stage_started.wait(), timeout=0.2)
    except TimeoutError:
        staged_while_delete_blocked = False
    else:
        staged_while_delete_blocked = True
    finally:
        storage.allow_delete.set()

    deleted, result = await asyncio.gather(retention_task, upload_task)

    assert not staged_while_delete_blocked
    assert deleted == 1
    assert result.created is True
    assert await storage.read(key) == image.content
    assert await storage.pending_writes() == ()
    async with sessions() as session:
        blobs = (await session.execute(select(ImageBlobRecord))).scalars().all()
        pages = (await session.execute(select(PageRecord))).scalars().all()
    assert len(blobs) == 1
    assert blobs[0].state == "ready"
    assert blobs[0].sha256 == bytes.fromhex(image.sha256)
    assert len(pages) == 1
    assert pages[0].image_blob_id == blobs[0].id
    await engine.dispose()
