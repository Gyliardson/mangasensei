"""Transient persistence for isolated local PDF imports."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mangasensei.infrastructure.database.base import Base


PDF_IMPORT_ERROR_CODES = (
    "pdf_invalid",
    "pdf_encrypted_unsupported",
    "pdf_page_limit",
    "pdf_geometry_limit",
    "pdf_pixel_limit",
    "pdf_raster_bytes_limit",
    "pdf_renderer_timeout",
    "pdf_renderer_crash",
    "pdf_temp_storage_exhausted",
    "pdf_render_failed",
    "pdf_raster_validation_failed",
    "pdf_manifest_invalid",
)


class DocumentImportRecord(Base):
    """One bounded source PDF admission and render/import operation."""

    __tablename__ = "document_imports"
    __table_args__ = (
        UniqueConstraint("upload_key_id", "upload_idempotency_digest"),
        CheckConstraint("source_kind = 'pdf'", name="source_kind"),
        CheckConstraint(
            "status IN ('queued','rendering','completed','failed')",
            name="status",
        ),
        CheckConstraint("octet_length(source_sha256) = 32", name="source_sha256_length"),
        CheckConstraint(
            "source_bytes > 0 AND source_bytes <= 268435456",
            name="source_bytes_range",
        ),
        CheckConstraint("octet_length(upload_idempotency_digest) = 32", name="idempotency_length"),
        CheckConstraint("octet_length(request_digest) = 32", name="request_digest_length"),
        CheckConstraint("fencing_token >= 0", name="fencing_nonnegative"),
        CheckConstraint("page_count IS NULL OR (page_count >= 1 AND page_count <= 200)", name="page_count"),
        CheckConstraint("expires_at = created_at + interval '24 hours'", name="retention_exact"),
        CheckConstraint(
            "source_expires_at = created_at + interval '1 hour'",
            name="source_retention_exact",
        ),
        CheckConstraint(
            "(status = 'completed' AND document_id IS NOT NULL AND error_code IS NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'failed' AND document_id IS NULL AND error_code IS NOT NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('queued','rendering') AND document_id IS NULL "
            "AND error_code IS NULL AND finished_at IS NULL)",
            name="terminal_state",
        ),
        CheckConstraint(
            "(status = 'rendering' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status != 'rendering' AND lease_owner IS NULL AND lease_until IS NULL)",
            name="lease_state",
        ),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            + ",".join(f"'{code}'" for code in PDF_IMPORT_ERROR_CODES)
            + ")",
            name="error_code",
        ),
        Index("ix_document_imports_status_lease", "status", "lease_until", "id"),
        Index("ix_document_imports_source_expiry", "source_expires_at", "id"),
        Index("ix_document_imports_expires_at_id", "expires_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True, server_default=text("uuidv4()")
    )
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pdf")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="queued")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    source_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    study_language: Mapped[str] = mapped_column(String(8), nullable=False)
    raster_contract: Mapped[str] = mapped_column(String(32), nullable=False)
    upload_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    upload_idempotency_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    page_count: Mapped[int | None] = mapped_column()
    renderer_pypdfium2: Mapped[str | None] = mapped_column(String(32))
    renderer_pdfium: Mapped[str | None] = mapped_column(String(64))
    renderer_pillow: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("mangasensei.documents.id", ondelete="CASCADE"), unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP + INTERVAL '24 hours'"),
    )
    source_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP + INTERVAL '1 hour'"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_cleaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentImportCapabilityRecord(Base):
    __tablename__ = "document_import_capabilities"
    __table_args__ = (
        UniqueConstraint("key_id", "digest"),
        CheckConstraint("scope = 'read:document-import'", name="scope"),
        CheckConstraint("octet_length(digest) = 32", name="digest_length"),
        Index("ix_document_import_capabilities_import_id", "document_import_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_import_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.document_imports.id", ondelete="CASCADE"), nullable=False
    )
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
