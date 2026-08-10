from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.infrastructure.database.document_models import DocumentRecord
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.retention import RetentionJanitor
from tests.integration.test_upload_api import make_settings, page_image


@pytest.mark.integration
@pytest.mark.asyncio
async def test_created_document_retention_preserves_shared_live_blob(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    image = page_image((84, 42, 21))
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        document_response = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "retention-created-document-0001"},
            data={"studyLanguage": "pt-BR"},
            files=[
                ("images[]", ("document-1.png", image, "image/png")),
                ("images[]", ("document-2.png", image, "image/png")),
            ],
        )
        standalone_response = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "retention-live-standalone-0001"},
            data={"studyLanguage": "pt-BR"},
            files={"image": ("standalone.png", image, "image/png")},
        )

    assert document_response.status_code == 202
    assert standalone_response.status_code == 202

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    storage = LocalFilesystemStorage(tmp_path)

    async with sessions.begin() as session:
        document = (await session.execute(select(DocumentRecord))).scalar_one()
        children = tuple(
            (
                await session.execute(
                    select(PageRecord)
                    .where(PageRecord.document_id == document.id)
                    .order_by(PageRecord.ordinal)
                )
            ).scalars()
        )
        standalone = (
            await session.execute(select(PageRecord).where(PageRecord.document_id.is_(None)))
        ).scalar_one()
        assert len(children) == 2
        assert all(child.created_at == document.created_at for child in children)
        assert all(child.expires_at == document.expires_at for child in children)
        assert all(child.image_blob_id == standalone.image_blob_id for child in children)
        child_ids = tuple(child.id for child in children)
        standalone_id = standalone.id
        blob_id = standalone.image_blob_id
        expired_at = datetime.now(UTC) - timedelta(seconds=1)
        document.expires_at = expired_at
        for child in children:
            child.expires_at = expired_at

    deleted = await RetentionJanitor(sessions, storage).run_once(batch_size=10)

    assert deleted >= 1
    async with sessions() as session:
        assert (await session.execute(select(DocumentRecord))).scalars().all() == []
        for child_id in child_ids:
            assert await session.get(PageRecord, child_id) is None
        assert await session.get(PageRecord, standalone_id) is not None
        assert await session.get(ImageBlobRecord, blob_id) is not None
    await engine.dispose()
