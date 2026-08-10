from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.infrastructure.database.document_models import DocumentRecord
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.retention import RetentionJanitor


def _image() -> ValidatedImage:
    content = b"document-retention-shared-blob"
    return ValidatedImage(
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        width=10,
        height=10,
        media_type="image/png",
        format="PNG",
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_document_cascades_children_without_deleting_shared_blob(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    image = _image()
    storage_key = await storage.store(image)
    document_created = datetime.now(UTC) - timedelta(hours=25)
    child_created = datetime.now(UTC) - timedelta(hours=1)

    async with sessions.begin() as session:
        blob = ImageBlobRecord(
            sha256=bytes.fromhex(image.sha256),
            byte_size=len(image.content),
            width=image.width,
            height=image.height,
            media_type=image.media_type,
            storage_key=storage_key,
        )
        document = DocumentRecord(
            created_at=document_created,
            expires_at=document_created + timedelta(hours=24),
        )
        session.add_all((blob, document))
        await session.flush()
        document_page = PageRecord(
            image_blob_id=blob.id,
            document_id=document.id,
            ordinal=0,
            original_filename="document.png",
            upload_key_id=None,
            upload_idempotency_digest=None,
            request_digest=bytes.fromhex(image.sha256),
            created_at=child_created,
            expires_at=child_created + timedelta(hours=24),
        )
        standalone_page = PageRecord(
            image_blob_id=blob.id,
            original_filename="standalone.png",
            upload_key_id="v1",
            upload_idempotency_digest=hashlib.sha256(b"standalone-page").digest(),
            request_digest=bytes.fromhex(image.sha256),
            created_at=child_created,
            expires_at=child_created + timedelta(hours=24),
        )
        session.add_all((document_page, standalone_page))
        await session.flush()
        document_page_id = document_page.id
        standalone_page_id = standalone_page.id

    deleted = await RetentionJanitor(sessions, storage).run_once(batch_size=10)

    assert deleted == 1
    async with sessions() as session:
        assert (await session.execute(select(DocumentRecord))).scalars().all() == []
        assert await session.get(PageRecord, document_page_id) is None
        assert await session.get(PageRecord, standalone_page_id) is not None
        blobs = (await session.execute(select(ImageBlobRecord))).scalars().all()
        assert len(blobs) == 1
    assert await storage.read(storage_key) == image.content
    await engine.dispose()
