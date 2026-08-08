from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.retention import RetentionJanitor


@pytest.mark.integration
@pytest.mark.asyncio
async def test_retention_deletes_expired_page_and_unreferenced_original(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    database_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)
    content = b"retention-original"
    digest = hashlib.sha256(content).hexdigest()
    key = await storage.store(
        ValidatedImage(
            content=content,
            sha256=digest,
            width=10,
            height=10,
            media_type="image/png",
            format="PNG",
        )
    )
    created_at = datetime.now(UTC) - timedelta(hours=25)
    async with sessions.begin() as session:
        blob = ImageBlobRecord(
            sha256=bytes.fromhex(digest),
            byte_size=len(content),
            width=10,
            height=10,
            media_type="image/png",
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
                request_digest=bytes.fromhex(digest),
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
