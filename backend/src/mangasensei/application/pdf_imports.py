"""Admission, capability query and fenced coordination for local PDF imports."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.application.authorization import ResourceNotFoundError
from mangasensei.application.document_uploads import DocumentCapabilityTokens
from mangasensei.application.idempotency import idempotency_digest
from mangasensei.application.uploads import IdempotencyConflictError, safe_filename, stage_image_blob
from mangasensei.config import Settings
from mangasensei.domain.capabilities import (
    DocumentCapabilityScope,
    DocumentImportCapabilityScope,
)
from mangasensei.domain.languages import StudyLanguage
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.document_import_models import (
    DocumentImportCapabilityRecord,
    DocumentImportRecord,
)
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_locks import acquire_image_blob_lock
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.pdf_imports.contracts import (
    PDFIUM_EXPECTED_BUILD,
    PDF_RASTER_CONTRACT_VERSION,
    PYPDFIUM2_EXPECTED_VERSION,
    PdfImportErrorCode,
    PdfRasterManifest,
    PdfRenderFailure,
    PdfRendererHeartbeat,
    PdfRenderRequest,
)
from mangasensei.pdf_imports.spool import PdfSpool, PdfSpoolError
from mangasensei.storage.images import ImageValidationError, ImageValidator, ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage, PendingStorageWrite

_READ_CHUNK_BYTES = 1024 * 1024
_HEARTBEAT_STALE_NS = 5_000_000_000


class PdfAdmissionError(ValueError):
    def __init__(self, code: PdfImportErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class DocumentImportCreateResult:
    import_id: UUID
    source_kind: str
    status: str
    raster_contract: str
    expires_at: datetime
    read_token: str
    created: bool


@dataclass(frozen=True, slots=True)
class DocumentImportView:
    import_id: UUID
    source_kind: str
    status: str
    raster_contract: str
    page_count: int | None
    error_code: str | None
    created_at: datetime
    expires_at: datetime
    document_id: UUID | None
    document_capabilities: DocumentCapabilityTokens | None


@dataclass(frozen=True, slots=True)
class _ClaimedImport:
    internal_id: int
    public_id: UUID
    fencing_token: int
    source_sha256: str
    study_language: StudyLanguage


class PdfImportService:
    """Streams bounded PDF source bytes and persists only transient import state."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        spool: PdfSpool,
        capabilities: CapabilityService,
        idempotency_pepper: str,
        max_pdf_bytes: int,
    ) -> None:
        self._sessions = sessions
        self._spool = spool
        self._capabilities = capabilities
        self._idempotency_pepper = idempotency_pepper.encode()
        self._max_pdf_bytes = max_pdf_bytes

    async def create(
        self,
        *,
        reader: Callable[[int], Awaitable[bytes]],
        declared_media_type: str | None,
        original_filename: str,
        idempotency_key: str,
        study_language: StudyLanguage,
    ) -> DocumentImportCreateResult:
        media_type = (declared_media_type or "").split(";", 1)[0].strip().lower()
        if media_type != "application/pdf":
            raise PdfAdmissionError("pdf_invalid", "PDF uploads must use application/pdf")

        staged_path, source_sha256, source_bytes = await self._stage_source(reader)
        upload_digest = idempotency_digest(
            pepper=self._idempotency_pepper,
            namespace="document-import-pdf",
            value=idempotency_key,
        )
        request_digest = pdf_import_request_digest(
            source_sha256=source_sha256,
            study_language=study_language,
        )
        new_public_id = uuid4()
        source_published = False
        try:
            destination_dir = self._spool.prepare_import_dir(new_public_id)
            destination = self._spool.source_path(new_public_id)
            if destination.exists() or destination.is_symlink():
                raise PdfSpoolError("generated PDF source destination already exists")
            os.replace(staged_path, destination)
            source_published = True

            async with self._sessions.begin() as session:
                inserted_id = (
                    await session.execute(
                        insert(DocumentImportRecord)
                        .values(
                            public_id=new_public_id,
                            source_kind="pdf",
                            status="queued",
                            original_filename=safe_filename(original_filename or "document.pdf"),
                            source_sha256=bytes.fromhex(source_sha256),
                            source_bytes=source_bytes,
                            study_language=study_language.value,
                            raster_contract=PDF_RASTER_CONTRACT_VERSION,
                            upload_key_id="v1",
                            upload_idempotency_digest=upload_digest,
                            request_digest=request_digest,
                        )
                        .on_conflict_do_nothing(
                            index_elements=[
                                DocumentImportRecord.upload_key_id,
                                DocumentImportRecord.upload_idempotency_digest,
                            ]
                        )
                        .returning(DocumentImportRecord.id)
                    )
                ).scalar_one_or_none()
                if inserted_id is None:
                    record = (
                        await session.execute(
                            select(DocumentImportRecord).where(
                                DocumentImportRecord.upload_key_id == "v1",
                                DocumentImportRecord.upload_idempotency_digest == upload_digest,
                            )
                        )
                    ).scalar_one()
                    if not hmac.compare_digest(record.request_digest, request_digest):
                        raise IdempotencyConflictError(
                            "idempotency key is bound to another PDF import request"
                        )
                    created = False
                else:
                    record = await session.get_one(DocumentImportRecord, inserted_id)
                    created = True
                token = await self._issue_import_capability(session, record)
                await session.flush()
                result = DocumentImportCreateResult(
                    import_id=record.public_id,
                    source_kind=record.source_kind,
                    status=record.status,
                    raster_contract=record.raster_contract,
                    expires_at=record.expires_at,
                    read_token=token,
                    created=created,
                )

            if not created:
                self._spool.remove_import(new_public_id)
            return result
        except Exception:
            staged_path.unlink(missing_ok=True)
            if source_published:
                with suppress(OSError, PdfSpoolError):
                    self._spool.remove_import(new_public_id)
            raise

    async def _stage_source(
        self, reader: Callable[[int], Awaitable[bytes]]
    ) -> tuple[Path, str, int]:
        staging = self._spool.root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        if staging.is_symlink():
            raise PdfSpoolError("PDF staging directory must not be a symlink")
        path = staging / f"{uuid4()}.pdf.part"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        digest = hashlib.sha256()
        total = 0
        prefix = bytearray()
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                while True:
                    chunk = await reader(_READ_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self._max_pdf_bytes:
                        raise PdfAdmissionError("pdf_invalid", "PDF input exceeds the byte limit")
                    if len(prefix) < 8:
                        prefix.extend(chunk[: 8 - len(prefix)])
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            os.close(descriptor)

        if total == 0 or not bytes(prefix).startswith(b"%PDF-"):
            path.unlink(missing_ok=True)
            raise PdfAdmissionError("pdf_invalid", "PDF header is invalid")
        return path, digest.hexdigest(), total

    async def _issue_import_capability(
        self, session: AsyncSession, record: DocumentImportRecord
    ) -> str:
        scope = DocumentImportCapabilityScope.READ_DOCUMENT_IMPORT
        issued = self._capabilities.issue(
            resource_id=str(record.public_id),
            scope=scope,
            expires_at=record.expires_at,
        )
        session.add(
            DocumentImportCapabilityRecord(
                document_import_id=record.id,
                key_id="v1",
                scope=scope.value,
                digest=bytes.fromhex(issued.persisted_digest),
                expires_at=issued.expires_at,
            )
        )
        return issued.token


class PdfImportQueryService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        capabilities: CapabilityService,
    ) -> None:
        self._sessions = sessions
        self._capabilities = capabilities

    async def get(self, *, import_id: UUID, token: str) -> DocumentImportView:
        async with self._sessions.begin() as session:
            record = await self._authorize(session, import_id=import_id, token=token)
            document_id: UUID | None = None
            document_capabilities: DocumentCapabilityTokens | None = None
            if record.status == "completed" and record.document_id is not None:
                document = await session.get(DocumentRecord, record.document_id)
                if document is None or document.expires_at <= datetime.now(UTC):
                    raise ResourceNotFoundError
                document_id = document.public_id
                document_capabilities = await self._issue_document_capabilities(session, document)
            return DocumentImportView(
                import_id=record.public_id,
                source_kind=record.source_kind,
                status=record.status,
                raster_contract=record.raster_contract,
                page_count=record.page_count,
                error_code=record.error_code,
                created_at=record.created_at,
                expires_at=record.expires_at,
                document_id=document_id,
                document_capabilities=document_capabilities,
            )

    async def _authorize(
        self, session: AsyncSession, *, import_id: UUID, token: str
    ) -> DocumentImportRecord:
        record = (
            await session.execute(
                select(DocumentImportRecord).where(
                    DocumentImportRecord.public_id == import_id,
                    DocumentImportRecord.expires_at > datetime.now(UTC),
                )
            )
        ).scalar_one_or_none()
        if record is None:
            raise ResourceNotFoundError
        scope = DocumentImportCapabilityScope.READ_DOCUMENT_IMPORT
        rows = (
            await session.execute(
                select(DocumentImportCapabilityRecord).where(
                    DocumentImportCapabilityRecord.document_import_id == record.id,
                    DocumentImportCapabilityRecord.scope == scope.value,
                    DocumentImportCapabilityRecord.revoked_at.is_(None),
                    DocumentImportCapabilityRecord.expires_at > datetime.now(UTC),
                )
            )
        ).scalars()
        if not any(
            self._capabilities.verify(
                token=token,
                persisted_digest=row.digest.hex(),
                resource_id=str(record.public_id),
                scope=scope,
                expires_at=row.expires_at,
            )
            for row in rows
        ):
            raise ResourceNotFoundError
        return record

    async def _issue_document_capabilities(
        self, session: AsyncSession, document: DocumentRecord
    ) -> DocumentCapabilityTokens:
        scopes = tuple(DocumentCapabilityScope)
        issued = {
            scope: self._capabilities.issue(
                resource_id=str(document.public_id),
                scope=scope,
                expires_at=document.expires_at,
            )
            for scope in scopes
        }
        session.add_all(
            DocumentCapabilityRecord(
                document_id=document.id,
                key_id="v1",
                scope=scope.value,
                digest=bytes.fromhex(capability.persisted_digest),
                expires_at=capability.expires_at,
            )
            for scope, capability in issued.items()
        )
        return DocumentCapabilityTokens(
            read_document=issued[DocumentCapabilityScope.READ_DOCUMENT].token,
            read_document_image=issued[DocumentCapabilityScope.READ_DOCUMENT_IMAGE].token,
            reprocess_document=issued[DocumentCapabilityScope.REPROCESS_DOCUMENT].token,
            manage_document=issued[DocumentCapabilityScope.MANAGE_DOCUMENT].token,
        )


class PdfImportCoordinator:
    """Owns PDF-import leases; the renderer itself never receives DB or application secrets."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFilesystemStorage,
        spool: PdfSpool,
        image_validator: ImageValidator,
        settings: Settings,
        idempotency_pepper: str,
        worker_id: str,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._spool = spool
        self._validator = image_validator
        self._settings = settings
        self._idempotency_pepper = idempotency_pepper.encode()
        self._worker_id = worker_id

    async def run_once(self) -> bool:
        await self.cleanup_once()
        claim = await self._claim()
        if claim is None:
            return False
        await self._process(claim)
        return True

    async def cleanup_once(self) -> None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            expired_sources = (
                await session.execute(
                    select(DocumentImportRecord)
                    .where(
                        DocumentImportRecord.source_cleaned_at.is_(None),
                        or_(
                            DocumentImportRecord.status.in_(("completed", "failed")),
                            DocumentImportRecord.source_expires_at <= now,
                        ),
                    )
                    .with_for_update(skip_locked=True)
                    .limit(100)
                )
            ).scalars().all()
            for record in expired_sources:
                if record.status in ("queued", "rendering") and record.source_expires_at <= now:
                    record.status = "failed"
                    record.error_code = "pdf_renderer_timeout"
                    record.finished_at = now
                    record.lease_owner = None
                    record.lease_until = None
                try:
                    self._spool.remove_import(record.public_id)
                except (OSError, PdfSpoolError):
                    continue
                record.source_cleaned_at = now
            await session.execute(
                delete(DocumentImportRecord).where(DocumentImportRecord.expires_at <= now)
            )

        await self._cleanup_orphan_spool(now)

    async def _cleanup_orphan_spool(self, now: datetime) -> None:
        cutoff = now.timestamp() - self._settings.pdf_source_ttl_seconds
        for directory in sorted(self._spool.imports.iterdir(), key=lambda item: item.name)[:100]:
            try:
                import_id = UUID(directory.name)
            except ValueError:
                continue
            try:
                if directory.lstat().st_mtime > cutoff:
                    continue
            except FileNotFoundError:
                continue
            async with self._sessions() as session:
                exists = (
                    await session.execute(
                        select(DocumentImportRecord.id).where(
                            DocumentImportRecord.public_id == import_id
                        )
                    )
                ).scalar_one_or_none()
            if exists is None:
                with suppress(OSError, PdfSpoolError):
                    self._spool.remove_import(import_id)

    async def _claim(self) -> _ClaimedImport | None:
        now = datetime.now(UTC)
        async with self._sessions.begin() as session:
            record = (
                await session.execute(
                    select(DocumentImportRecord)
                    .where(
                        DocumentImportRecord.expires_at > now,
                        DocumentImportRecord.source_expires_at > now,
                        or_(
                            DocumentImportRecord.status == "queued",
                            (
                                (DocumentImportRecord.status == "rendering")
                                & (DocumentImportRecord.lease_until < now)
                            ),
                        ),
                    )
                    .order_by(DocumentImportRecord.created_at, DocumentImportRecord.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if record is None:
                return None
            record.status = "rendering"
            record.fencing_token += 1
            record.lease_owner = self._worker_id
            record.lease_until = now + timedelta(seconds=self._settings.pdf_import_lease_seconds)
            await session.flush()
            return _ClaimedImport(
                internal_id=record.id,
                public_id=record.public_id,
                fencing_token=record.fencing_token,
                source_sha256=record.source_sha256.hex(),
                study_language=StudyLanguage(record.study_language),
            )

    async def _process(self, claim: _ClaimedImport) -> None:
        try:
            self._spool.require_regular_file(
                self._spool.source_path(claim.public_id), max_bytes=self._settings.max_pdf_bytes
            )
            request = PdfRenderRequest(
                import_id=claim.public_id,
                fencing_token=claim.fencing_token,
                source_sha256=claim.source_sha256,
                max_pages=self._settings.max_pdf_pages,
                max_side=self._settings.max_image_side,
                max_page_pixels=self._settings.max_image_pixels,
                max_aggregate_pixels=self._settings.max_document_pixels,
                max_page_raster_bytes=self._settings.max_upload_bytes,
                max_aggregate_raster_bytes=self._settings.max_pdf_raster_bytes,
                max_spool_bytes=self._settings.max_pdf_spool_bytes,
            )
            self._spool.prepare_attempt_dir(claim.public_id, claim.fencing_token)
            self._spool.write_model_atomic(
                self._spool.request_path(claim.public_id, claim.fencing_token), request
            )
        except (OSError, PdfSpoolError, ValidationError):
            await self._terminal_failure(claim, "pdf_invalid")
            return

        outcome = await self._wait_for_renderer(claim)
        if isinstance(outcome, str):
            await self._terminal_failure(claim, outcome)
            return
        try:
            images = self._validate_manifest(claim, outcome)
        except (ImageValidationError, PdfSpoolError, ValidationError, ValueError):
            await self._terminal_failure(claim, "pdf_raster_validation_failed")
            return
        await self._commit_document(claim, outcome, images)

    async def _wait_for_renderer(
        self, claim: _ClaimedImport
    ) -> PdfRasterManifest | PdfImportErrorCode:
        deadline = time.monotonic() + self._settings.pdf_renderer_timeout_seconds
        started = time.monotonic()
        while time.monotonic() < deadline:
            manifest_path = self._spool.manifest_path(claim.public_id, claim.fencing_token)
            failure_path = self._spool.failure_path(claim.public_id, claim.fencing_token)
            if manifest_path.exists():
                try:
                    return PdfRasterManifest.model_validate(self._spool.read_json(manifest_path))
                except (PdfSpoolError, ValidationError):
                    return "pdf_manifest_invalid"
            if failure_path.exists():
                try:
                    failure = PdfRenderFailure.model_validate(self._spool.read_json(failure_path))
                    if (
                        failure.import_id != claim.public_id
                        or failure.fencing_token != claim.fencing_token
                    ):
                        return "pdf_manifest_invalid"
                    return failure.error_code
                except (PdfSpoolError, ValidationError):
                    return "pdf_manifest_invalid"
            if time.monotonic() - started >= 5:
                try:
                    heartbeat = PdfRendererHeartbeat.model_validate(
                        self._spool.read_json(self._spool.heartbeat_path(), max_bytes=64 * 1024)
                    )
                    if time.monotonic_ns() - heartbeat.monotonic_ns > _HEARTBEAT_STALE_NS:
                        return "pdf_renderer_crash"
                except (PdfSpoolError, ValidationError):
                    return "pdf_renderer_crash"
            await asyncio.sleep(min(0.25, self._settings.pdf_import_poll_seconds))
        return "pdf_renderer_timeout"

    def _validate_manifest(
        self, claim: _ClaimedImport, manifest: PdfRasterManifest
    ) -> tuple[ValidatedImage, ...]:
        if (
            manifest.import_id != claim.public_id
            or manifest.fencing_token != claim.fencing_token
            or manifest.source_sha256 != claim.source_sha256
            or manifest.raster_contract != PDF_RASTER_CONTRACT_VERSION
            or manifest.renderer.pypdfium2 != PYPDFIUM2_EXPECTED_VERSION
            or manifest.renderer.pdfium_build != PDFIUM_EXPECTED_BUILD
            or manifest.page_count != len(manifest.pages)
            or manifest.page_count > self._settings.max_pdf_pages
        ):
            raise PdfSpoolError("manifest identity or renderer provenance mismatch")
        if "V8" in manifest.renderer.pdfium_flags or "XFA" in manifest.renderer.pdfium_flags:
            raise PdfSpoolError("unsupported PDFium feature build")

        expected_ordinals = tuple(range(manifest.page_count))
        if tuple(page.ordinal for page in manifest.pages) != expected_ordinals:
            raise PdfSpoolError("manifest page order is not contiguous")

        images: list[ValidatedImage] = []
        aggregate_bytes = 0
        aggregate_pixels = 0
        for page in manifest.pages:
            path = self._spool.page_path(claim.public_id, claim.fencing_token, page.filename)
            metadata = self._spool.require_regular_file(
                path, max_bytes=self._settings.max_upload_bytes
            )
            if metadata.st_size != page.byte_size:
                raise PdfSpoolError("raster size mismatch")
            content = path.read_bytes()
            if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), page.sha256):
                raise PdfSpoolError("raster hash mismatch")
            image = self._validator.validate(content, declared_media_type="image/png")
            if image.sha256 != page.sha256 or image.width != page.width or image.height != page.height:
                raise PdfSpoolError("raster validation metadata mismatch")
            aggregate_bytes += len(content)
            aggregate_pixels += image.width * image.height
            if aggregate_bytes > self._settings.max_pdf_raster_bytes:
                raise PdfSpoolError("aggregate raster byte limit exceeded")
            if aggregate_pixels > self._settings.max_document_pixels:
                raise PdfSpoolError("aggregate raster pixel limit exceeded")
            images.append(image)
        if (
            aggregate_bytes != manifest.aggregate_raster_bytes
            or aggregate_pixels != manifest.aggregate_pixels
        ):
            raise PdfSpoolError("manifest aggregate mismatch")
        return tuple(images)

    async def _commit_document(
        self,
        claim: _ClaimedImport,
        manifest: PdfRasterManifest,
        images: tuple[ValidatedImage, ...],
    ) -> None:
        pending_writes: list[PendingStorageWrite] = []
        committed = False
        async with self._sessions.begin() as session:
            record = (
                await session.execute(
                    select(DocumentImportRecord)
                    .where(DocumentImportRecord.id == claim.internal_id)
                    .with_for_update()
                )
            ).scalar_one()
            if (
                record.status != "rendering"
                or record.fencing_token != claim.fencing_token
                or record.lease_owner != self._worker_id
            ):
                return

            for digest in sorted({bytes.fromhex(image.sha256) for image in images}):
                await acquire_image_blob_lock(session, digest)

            document = DocumentRecord(
                source_kind="pdf",
                upload_key_id=record.upload_key_id,
                upload_idempotency_digest=record.upload_idempotency_digest,
                request_digest=record.request_digest,
                created_at=record.created_at,
                expires_at=record.expires_at,
            )
            session.add(document)
            await session.flush()

            for ordinal, image in enumerate(images):
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
                    original_filename=f"page-{ordinal + 1:06d}.png",
                    upload_key_id=None,
                    upload_idempotency_digest=None,
                    request_digest=bytes.fromhex(image.sha256),
                    created_at=record.created_at,
                    expires_at=record.expires_at,
                )
                session.add(page)
                await session.flush()
                session.add(
                    JobRecord(
                        page_id=page.id,
                        idempotency_digest=self._child_job_digest(
                            record.upload_idempotency_digest, ordinal
                        ),
                        request_digest=page.request_digest,
                        study_language=claim.study_language.value,
                    )
                )

            record.status = "completed"
            record.document_id = document.id
            record.page_count = manifest.page_count
            record.renderer_pypdfium2 = manifest.renderer.pypdfium2
            record.renderer_pdfium = manifest.renderer.pdfium
            record.renderer_pillow = manifest.renderer.pillow
            record.finished_at = datetime.now(UTC)
            record.lease_owner = None
            record.lease_until = None
            await session.flush()
            committed = True

        if not committed:
            with suppress(OSError, PdfSpoolError):
                self._spool.remove_attempt(claim.public_id, claim.fencing_token)
            return
        for pending in pending_writes:
            with suppress(OSError):
                await self._storage.confirm(pending)
        await self._cleanup_terminal_source(claim.public_id)

    async def _terminal_failure(
        self, claim: _ClaimedImport, code: PdfImportErrorCode
    ) -> None:
        changed = False
        async with self._sessions.begin() as session:
            record = (
                await session.execute(
                    select(DocumentImportRecord)
                    .where(DocumentImportRecord.id == claim.internal_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if (
                record is not None
                and record.status == "rendering"
                and record.fencing_token == claim.fencing_token
                and record.lease_owner == self._worker_id
            ):
                record.status = "failed"
                record.error_code = code
                record.finished_at = datetime.now(UTC)
                record.lease_owner = None
                record.lease_until = None
                changed = True
        if changed:
            await self._cleanup_terminal_source(claim.public_id)
        else:
            with suppress(OSError, PdfSpoolError):
                self._spool.remove_attempt(claim.public_id, claim.fencing_token)

    async def _cleanup_terminal_source(self, import_id: UUID) -> None:
        try:
            self._spool.remove_import(import_id)
        except (OSError, PdfSpoolError):
            return
        async with self._sessions.begin() as session:
            record = (
                await session.execute(
                    select(DocumentImportRecord)
                    .where(DocumentImportRecord.public_id == import_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if record is not None and record.status in ("completed", "failed"):
                record.source_cleaned_at = datetime.now(UTC)

    def _child_job_digest(self, upload_digest: bytes, ordinal: int) -> bytes:
        message = b"mangasensei:pdf-document-job:v1\0" + upload_digest + ordinal.to_bytes(8, "big")
        return hmac.new(self._idempotency_pepper, message, hashlib.sha256).digest()


def pdf_import_request_digest(
    *, source_sha256: str, study_language: StudyLanguage
) -> bytes:
    digest = hashlib.sha256()
    digest.update(b"mangasensei:document-import-request:v1\0pdf\0")
    digest.update(PDF_RASTER_CONTRACT_VERSION.encode("ascii"))
    digest.update(b"\0")
    language = study_language.value.encode("ascii")
    digest.update(len(language).to_bytes(2, "big"))
    digest.update(language)
    digest.update(bytes.fromhex(source_sha256))
    return digest.digest()
