"""Crash-safe 24-hour page and unreferenced blob cleanup."""

from __future__ import annotations

from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.infrastructure.database.operational_models import RateLimitBucketRecord
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.local import LocalFilesystemStorage


class RetentionJanitor:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFilesystemStorage,
    ) -> None:
        self._sessions = sessions
        self._storage = storage

    async def run_once(self, *, batch_size: int = 100) -> int:
        if not 1 <= batch_size <= 1000:
            raise ValueError("retention batch size must be between 1 and 1000")
        async with self._sessions.begin() as session:
            await session.execute(
                delete(RateLimitBucketRecord).where(
                    RateLimitBucketRecord.window_start < func.now() - text("interval '1 day'")
                )
            )
            expired = tuple(
                (
                    await session.execute(
                        select(PageRecord.id, PageRecord.image_blob_id)
                        .where(PageRecord.expires_at <= func.now())
                        .order_by(PageRecord.expires_at, PageRecord.id)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if not expired:
                return 0
            page_ids = tuple(row.id for row in expired)
            blob_ids = frozenset(row.image_blob_id for row in expired)
            await session.execute(delete(PageRecord).where(PageRecord.id.in_(page_ids)))

        for blob_id in blob_ids:
            await self._delete_if_unreferenced(blob_id)
        return len(expired)

    async def _delete_if_unreferenced(self, blob_id: int) -> None:
        async with self._sessions.begin() as session:
            blob = await session.get(ImageBlobRecord, blob_id, with_for_update=True)
            if blob is None:
                return
            referenced = (
                await session.execute(select(exists().where(PageRecord.image_blob_id == blob_id)))
            ).scalar_one()
            if referenced:
                return
            blob.state = "deleting"
            storage_key = blob.storage_key

        await self._storage.delete(storage_key)

        async with self._sessions.begin() as session:
            await session.execute(
                delete(ImageBlobRecord).where(
                    ImageBlobRecord.id == blob_id,
                    ImageBlobRecord.state == "deleting",
                    ~exists().where(PageRecord.image_blob_id == blob_id),
                )
            )
