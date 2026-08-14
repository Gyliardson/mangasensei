"""Crash-safe 24-hour document, page and unreferenced blob cleanup."""

from __future__ import annotations

from sqlalchemy import delete, exists, func, select, text, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.infrastructure.database.document_models import DocumentRecord
from mangasensei.infrastructure.database.gemini_accounting import (
    reconcile_abandoned_gemini_calls,
)
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

        document_pages = (
            select(
                PageRecord.id.label("page_id"),
                DocumentRecord.expires_at.label("effective_expires_at"),
            )
            .join(DocumentRecord, DocumentRecord.id == PageRecord.document_id)
            .where(DocumentRecord.expires_at <= func.now())
        )
        standalone_pages = select(
            PageRecord.id.label("page_id"),
            PageRecord.expires_at.label("effective_expires_at"),
        ).where(
            PageRecord.document_id.is_(None),
            PageRecord.expires_at <= func.now(),
        )
        eligible_pages = union_all(document_pages, standalone_pages).subquery()

        async with self._sessions.begin() as session:
            stale_bucket_keys = tuple(
                tuple(row)
                for row in (
                    await session.execute(
                        select(
                            RateLimitBucketRecord.key_digest,
                            RateLimitBucketRecord.action,
                            RateLimitBucketRecord.window_start,
                        )
                        .where(
                            RateLimitBucketRecord.window_start
                            < func.now() - text("interval '1 day'")
                        )
                        .order_by(
                            RateLimitBucketRecord.window_start,
                            RateLimitBucketRecord.action,
                            RateLimitBucketRecord.key_digest,
                        )
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if stale_bucket_keys:
                await session.execute(
                    delete(RateLimitBucketRecord).where(
                        tuple_(
                            RateLimitBucketRecord.key_digest,
                            RateLimitBucketRecord.action,
                            RateLimitBucketRecord.window_start,
                        ).in_(stale_bucket_keys)
                    )
                )

            expired_pages = tuple(
                (
                    await session.execute(
                        select(PageRecord.id, PageRecord.image_blob_id)
                        .join(eligible_pages, eligible_pages.c.page_id == PageRecord.id)
                        .order_by(eligible_pages.c.effective_expires_at, PageRecord.id)
                        .limit(batch_size)
                        .with_for_update(of=PageRecord, skip_locked=True)
                    )
                ).all()
            )
            if expired_pages:
                page_ids = tuple(row.id for row in expired_pages)
                await reconcile_abandoned_gemini_calls(session, page_ids=page_ids)
                await session.execute(delete(PageRecord).where(PageRecord.id.in_(page_ids)))

            empty_expired_document_ids = tuple(
                (
                    await session.execute(
                        select(DocumentRecord.id)
                        .where(
                            DocumentRecord.expires_at <= func.now(),
                            ~exists().where(PageRecord.document_id == DocumentRecord.id),
                        )
                        .order_by(DocumentRecord.expires_at, DocumentRecord.id)
                        .limit(batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()
            )
            if empty_expired_document_ids:
                await session.execute(
                    delete(DocumentRecord).where(
                        DocumentRecord.id.in_(empty_expired_document_ids)
                    )
                )

            deleted_blob_ids = tuple(dict.fromkeys(row.image_blob_id for row in expired_pages))
            newly_orphaned_blob_ids = (
                tuple(
                    (
                        await session.execute(
                            select(ImageBlobRecord.id)
                            .where(
                                ImageBlobRecord.id.in_(deleted_blob_ids),
                                ~exists().where(PageRecord.image_blob_id == ImageBlobRecord.id),
                            )
                            .order_by(ImageBlobRecord.id)
                            .limit(batch_size)
                        )
                    ).scalars()
                )
                if deleted_blob_ids
                else ()
            )
            remaining_blob_budget = batch_size - len(newly_orphaned_blob_ids)
            older_orphan_blob_ids = (
                tuple(
                    (
                        await session.execute(
                            select(ImageBlobRecord.id)
                            .where(
                                ~exists().where(
                                    PageRecord.image_blob_id == ImageBlobRecord.id
                                ),
                                ImageBlobRecord.id.notin_(newly_orphaned_blob_ids),
                            )
                            .order_by(ImageBlobRecord.id)
                            .limit(remaining_blob_budget)
                        )
                    ).scalars()
                )
                if remaining_blob_budget > 0 and newly_orphaned_blob_ids
                else (
                    tuple(
                        (
                            await session.execute(
                                select(ImageBlobRecord.id)
                                .where(
                                    ~exists().where(
                                        PageRecord.image_blob_id == ImageBlobRecord.id
                                    )
                                )
                                .order_by(ImageBlobRecord.id)
                                .limit(remaining_blob_budget)
                            )
                        ).scalars()
                    )
                    if remaining_blob_budget > 0
                    else ()
                )
            )

        blob_ids = (*newly_orphaned_blob_ids, *older_orphan_blob_ids)
        for blob_id in blob_ids:
            await self._delete_if_unreferenced(blob_id)
        return len(expired_pages)

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
