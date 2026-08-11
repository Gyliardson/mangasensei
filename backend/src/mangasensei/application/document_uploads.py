"""Atomic ordered multi-image Document creation."""

from __future__ import annotations

import hashlib
import hmac
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.application.idempotency import idempotency_digest
from mangasensei.application.uploads import (
    IdempotencyConflictError,
    safe_filename,
    stage_image_blob,
)
from mangasensei.domain.capabilities import DocumentCapabilityScope
from mangasensei.domain.languages import StudyLanguage
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_locks import acquire_image_blob_lock
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.storage.images import ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage, PendingStorageWrite


@dataclass(frozen=True, slots=True)
class DocumentCapabilityTokens:
    read_document: str
    read_document_image: str
    reprocess_document: str
    manage_document: str


@dataclass(frozen=True, slots=True)
class DocumentCreateResult:
    internal_id: int
    document_id: UUID
    source_kind: str
    order_revision: int
    expires_at: datetime
    capabilities: DocumentCapabilityTokens
    created: bool


class DocumentUploadService:
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
        images: tuple[ValidatedImage, ...],
        original_filenames: tuple[str, ...],
        idempotency_key: str,
        study_language: StudyLanguage,
    ) -> DocumentCreateResult:
        if not images or len(images) != len(original_filenames):
            raise ValueError("document requires at least one image")
        upload_digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="document-upload",
            value=idempotency_key,
        )
        request_digest = document_request_digest(images=images, study_language=study_language)
        pending_writes: list[PendingStorageWrite] = []

        async with self._sessions.begin() as session:
            document, created = await self._get_or_create_document(
                session,
                upload_digest=upload_digest,
                request_digest=request_digest,
            )
            if created:
                for digest in sorted({bytes.fromhex(image.sha256) for image in images}):
                    await acquire_image_blob_lock(session, digest)
                ordered_inputs = zip(images, original_filenames, strict=True)
                for ordinal, (image, filename) in enumerate(ordered_inputs):
                    blob, pending = await stage_image_blob(
                        session,
                        storage=self._storage,
                        image=image,
                    )
                    pending_writes.append(pending)
                    page = PageRecord(
                        image_blob_id=blob.id,
                        document_id=document.id,
                        ordinal=ordinal,
                        original_filename=safe_filename(filename),
                        upload_key_id=None,
                        upload_idempotency_digest=None,
                        request_digest=bytes.fromhex(image.sha256),
                        created_at=document.created_at,
                        expires_at=document.expires_at,
                    )
                    session.add(page)
                    await session.flush()
                    session.add(
                        JobRecord(
                            page_id=page.id,
                            idempotency_digest=self._child_job_digest(upload_digest, ordinal),
                            request_digest=page.request_digest,
                            study_language=study_language.value,
                        )
                    )
                await session.flush()
            elif document.request_digest is None or not hmac.compare_digest(
                document.request_digest, request_digest
            ):
                raise IdempotencyConflictError(
                    "idempotency key is bound to another document request"
                )
            tokens = await self._issue_capabilities(session, document)
            await session.flush()
            result = DocumentCreateResult(
                internal_id=document.id,
                document_id=document.public_id,
                source_kind=document.source_kind,
                order_revision=document.order_revision,
                expires_at=document.expires_at,
                capabilities=tokens,
                created=created,
            )

        for pending in pending_writes:
            with suppress(OSError):
                await self._storage.confirm(pending)
        return result

    async def _get_or_create_document(
        self,
        session: AsyncSession,
        *,
        upload_digest: bytes,
        request_digest: bytes,
    ) -> tuple[DocumentRecord, bool]:
        inserted_id = (
            await session.execute(
                insert(DocumentRecord)
                .values(
                    source_kind="images",
                    upload_key_id="v1",
                    upload_idempotency_digest=upload_digest,
                    request_digest=request_digest,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        DocumentRecord.upload_key_id,
                        DocumentRecord.upload_idempotency_digest,
                    ]
                )
                .returning(DocumentRecord.id)
            )
        ).scalar_one_or_none()
        if inserted_id is not None:
            return await session.get_one(DocumentRecord, inserted_id), True
        existing = (
            await session.execute(
                select(DocumentRecord).where(
                    DocumentRecord.upload_key_id == "v1",
                    DocumentRecord.upload_idempotency_digest == upload_digest,
                )
            )
        ).scalar_one()
        if existing.request_digest is None or not hmac.compare_digest(
            existing.request_digest, request_digest
        ):
            raise IdempotencyConflictError("idempotency key is bound to another document request")
        return existing, False

    async def _issue_capabilities(
        self,
        session: AsyncSession,
        document: DocumentRecord,
    ) -> DocumentCapabilityTokens:
        scopes = (
            DocumentCapabilityScope.READ_DOCUMENT,
            DocumentCapabilityScope.READ_DOCUMENT_IMAGE,
            DocumentCapabilityScope.REPROCESS_DOCUMENT,
            DocumentCapabilityScope.MANAGE_DOCUMENT,
        )
        issued = {
            scope: self._capabilities.issue(
                resource_id=str(document.public_id),
                scope=scope,
                expires_at=document.expires_at,
            )
            for scope in scopes
        }
        session.add_all(
            [
                DocumentCapabilityRecord(
                    document_id=document.id,
                    key_id="v1",
                    scope=scope.value,
                    digest=bytes.fromhex(capability.persisted_digest),
                    expires_at=capability.expires_at,
                )
                for scope, capability in issued.items()
            ]
        )
        return DocumentCapabilityTokens(
            read_document=issued[DocumentCapabilityScope.READ_DOCUMENT].token,
            read_document_image=issued[DocumentCapabilityScope.READ_DOCUMENT_IMAGE].token,
            reprocess_document=issued[DocumentCapabilityScope.REPROCESS_DOCUMENT].token,
            manage_document=issued[DocumentCapabilityScope.MANAGE_DOCUMENT].token,
        )

    def _child_job_digest(self, upload_digest: bytes, ordinal: int) -> bytes:
        message = b"mangasensei:document-job:v1\0" + upload_digest + ordinal.to_bytes(8, "big")
        return hmac.new(self._idempotency_pepper, message, hashlib.sha256).digest()


def document_request_digest(
    *,
    images: tuple[ValidatedImage, ...],
    study_language: StudyLanguage,
) -> bytes:
    """Digest ordered semantic request identity without concatenating source bytes."""
    digest = hashlib.sha256()
    digest.update(b"mangasensei:document-request:v1\0images\0")
    language = study_language.value.encode()
    digest.update(len(language).to_bytes(2, "big"))
    digest.update(language)
    digest.update(len(images).to_bytes(4, "big"))
    for image in images:
        digest.update(bytes.fromhex(image.sha256))
    return digest.digest()
