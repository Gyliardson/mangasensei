"""Transactional upload creation with blob deduplication and isolated pages."""

from __future__ import annotations

import hmac
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.application.idempotency import idempotency_digest
from mangasensei.domain.capabilities import CapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_locks import acquire_image_blob_lock
from mangasensei.infrastructure.database.storage_models import (
    ImageBlobRecord,
    PageCapabilityRecord,
    PageRecord,
)
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage


class IdempotencyConflictError(ValueError):
    """An idempotency key was reused for a different request."""


class StorageMetadataConflictError(RuntimeError):
    """A content digest resolved to incompatible immutable metadata."""


@dataclass(frozen=True, slots=True)
class CapabilityTokens:
    read_page: str
    read_image: str
    reprocess_page: str


@dataclass(frozen=True, slots=True)
class UploadResult:
    page_id: UUID
    job_id: UUID
    content_sha256: str
    width: int
    height: int
    media_type: str
    expires_at: datetime
    capabilities: CapabilityTokens
    created: bool


class UploadService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFilesystemStorage,
        capability_service: CapabilityService,
        idempotency_pepper: str,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._capabilities = capability_service
        self._idempotency_pepper = idempotency_pepper.encode()

    async def create(
        self,
        *,
        image: ValidatedImage,
        original_filename: str,
        idempotency_key: str,
    ) -> UploadResult:
        upload_digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="upload",
            value=idempotency_key,
        )
        job_digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="job",
            value=idempotency_key,
        )
        request_digest = bytes.fromhex(image.sha256)
        safe_filename = _safe_filename(original_filename)

        async with self._sessions.begin() as session:
            await acquire_image_blob_lock(session, request_digest)
            pending_write = await self._storage.stage(image)
            blob = await self._get_or_create_blob(session, image, pending_write.storage_key)
            page, created = await self._get_or_create_page(
                session,
                blob_id=blob.id,
                filename=safe_filename,
                upload_digest=upload_digest,
                request_digest=request_digest,
            )
            if created:
                job = JobRecord(
                    page_id=page.id,
                    idempotency_digest=job_digest,
                    request_digest=request_digest,
                )
                session.add(job)
                await session.flush()
            else:
                job = (
                    await session.execute(
                        select(JobRecord)
                        .where(JobRecord.page_id == page.id)
                        .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                        .limit(1)
                    )
                ).scalar_one()
            tokens = await self._issue_capabilities(session, page)
            await session.flush()
            result = UploadResult(
                page_id=page.public_id,
                job_id=job.public_id,
                content_sha256=image.sha256,
                width=image.width,
                height=image.height,
                media_type=image.media_type,
                expires_at=page.expires_at,
                capabilities=tokens,
                created=created,
            )

        # The database commit is authoritative. If marker cleanup itself fails, the
        # retention janitor will reconcile the marker without deleting a referenced blob.
        with suppress(OSError):
            await self._storage.confirm(pending_write)
        return result

    async def _get_or_create_blob(
        self,
        session: AsyncSession,
        image: ValidatedImage,
        storage_key: str,
    ) -> ImageBlobRecord:
        values = {
            "sha256": bytes.fromhex(image.sha256),
            "byte_size": len(image.content),
            "width": image.width,
            "height": image.height,
            "media_type": image.media_type,
            "storage_key": storage_key,
        }
        inserted_id = (
            await session.execute(
                insert(ImageBlobRecord)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[ImageBlobRecord.sha256])
                .returning(ImageBlobRecord.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            return await session.get_one(ImageBlobRecord, inserted_id)
        existing = (
            await session.execute(
                select(ImageBlobRecord).where(ImageBlobRecord.sha256 == values["sha256"])
            )
        ).scalar_one()
        immutable_metadata = (
            existing.byte_size,
            existing.width,
            existing.height,
            existing.media_type,
            existing.storage_key,
        )
        expected_metadata = (
            values["byte_size"],
            values["width"],
            values["height"],
            values["media_type"],
            values["storage_key"],
        )
        if immutable_metadata != expected_metadata:
            raise StorageMetadataConflictError("immutable blob metadata conflict")
        if existing.state == "deleting":
            existing.state = "ready"
        elif existing.state != "ready":
            raise StorageMetadataConflictError("immutable blob metadata conflict")
        return existing

    async def _get_or_create_page(
        self,
        session: AsyncSession,
        *,
        blob_id: int,
        filename: str,
        upload_digest: bytes,
        request_digest: bytes,
    ) -> tuple[PageRecord, bool]:
        inserted_id = (
            await session.execute(
                insert(PageRecord)
                .values(
                    image_blob_id=blob_id,
                    original_filename=filename,
                    upload_key_id="v1",
                    upload_idempotency_digest=upload_digest,
                    request_digest=request_digest,
                )
                .on_conflict_do_nothing(
                    index_elements=[PageRecord.upload_key_id, PageRecord.upload_idempotency_digest]
                )
                .returning(PageRecord.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            return await session.get_one(PageRecord, inserted_id), True
        existing = (
            await session.execute(
                select(PageRecord).where(
                    PageRecord.upload_key_id == "v1",
                    PageRecord.upload_idempotency_digest == upload_digest,
                )
            )
        ).scalar_one()
        if not hmac.compare_digest(existing.request_digest, request_digest):
            raise IdempotencyConflictError("idempotency key is bound to another request")
        return existing, False

    async def _issue_capabilities(
        self, session: AsyncSession, page: PageRecord
    ) -> CapabilityTokens:
        issued_by_scope = {
            scope: self._capabilities.issue(
                resource_id=str(page.public_id), scope=scope, expires_at=page.expires_at
            )
            for scope in CapabilityScope
        }
        session.add_all(
            [
                PageCapabilityRecord(
                    page_id=page.id,
                    key_id="v1",
                    scope=scope.value,
                    digest=bytes.fromhex(issued.persisted_digest),
                    expires_at=issued.expires_at,
                )
                for scope, issued in issued_by_scope.items()
            ]
        )
        return CapabilityTokens(
            read_page=issued_by_scope[CapabilityScope.READ_PAGE].token,
            read_image=issued_by_scope[CapabilityScope.READ_IMAGE].token,
            reprocess_page=issued_by_scope[CapabilityScope.REPROCESS_PAGE].token,
        )


def _safe_filename(filename: str) -> str:
    basename = Path(filename or "page").name
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return (cleaned or "page")[:255]
