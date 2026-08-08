"""Crash-safe 24-hour page and unreferenced blob cleanup."""

from __future__ import annotations

from sqlalchemy import delete, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.infrastructure.database.operational_models import RateLimitBucketRecord
from mangasensei.infrastructure.database.storage_locks import acquire_image_blob_lock
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.local import LocalFilesystemStorage, PendingStorageWrite


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

        for pending in await self._storage.pending_writes(limit=batch_size):
            await self._reconcile_pending_write(pending)

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
            if expired:
                page_ids = tuple(row.id for row in expired)
                await session.execute(delete(PageRecord).where(PageRecord.id.in_(page_ids)))
            expired_blob_ids = tuple(dict.fromkeys(row.image_blob_id for row in expired))
            unreferenced_blob_ids = tuple(
                (
                    await session.execute(
                        select(ImageBlobRecord.id)
                        .where(~exists().where(PageRecord.image_blob_id == ImageBlobRecord.id))
                        .order_by(ImageBlobRecord.id)
                        .limit(batch_size)
                    )
                ).scalars()
            )

        blob_ids = tuple(dict.fromkeys((*expired_blob_ids, *unreferenced_blob_ids)))
        for blob_id in blob_ids:
            await self._delete_if_unreferenced(blob_id)
        return len(expired)

    async def _reconcile_pending_write(self, pending: PendingStorageWrite) -> None:
        digest = bytes.fromhex(pending.sha256)
        async with self._sessions.begin() as session:
            await acquire_image_blob_lock(session, digest)
            known_blob = (
                await session.execute(select(exists().where(ImageBlobRecord.sha256 == digest)))
            ).scalar_one()
            if not known_blob:
                await self._storage.delete(pending.storage_key)
            await self._storage.confirm(pending)

    async def _delete_if_unreferenced(self, blob_id: int) -> None:
        async with self._sessions.begin() as session:
            digest = (
                await session.execute(
                    select(ImageBlobRecord.sha256).where(ImageBlobRecord.id == blob_id)
                )
            ).scalar_one_or_none()
            if digest is None:
                return

            await acquire_image_blob_lock(session, digest)
            blob = (
                await session.execute(
                    select(ImageBlobRecord)
                    .where(ImageBlobRecord.id == blob_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if blob is None:
                return
            referenced = (
                await session.execute(select(exists().where(PageRecord.image_blob_id == blob_id)))
            ).scalar_one()
            if referenced:
                if blob.state == "deleting":
                    blob.state = "ready"
                return

            blob.state = "deleting"
            await self._storage.delete(blob.storage_key)
            await session.delete(blob)
