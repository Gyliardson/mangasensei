"""Persistence records for document aggregates and capabilities."""

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


class DocumentRecord(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("upload_key_id", "upload_idempotency_digest"),
        CheckConstraint("source_kind IN ('images')", name="source_kind"),
        CheckConstraint("order_revision > 0", name="order_revision_positive"),
        CheckConstraint("expires_at = created_at + interval '24 hours'", name="retention_exact"),
        CheckConstraint(
            "(upload_key_id IS NULL AND upload_idempotency_digest IS NULL "
            "AND request_digest IS NULL) OR "
            "(upload_key_id IS NOT NULL AND upload_idempotency_digest IS NOT NULL "
            "AND request_digest IS NOT NULL)",
            name="creation_identity_triplet",
        ),
        CheckConstraint(
            "upload_idempotency_digest IS NULL OR octet_length(upload_idempotency_digest) = 32",
            name="idempotency_digest_length",
        ),
        CheckConstraint(
            "request_digest IS NULL OR octet_length(request_digest) = 32",
            name="request_digest_length",
        ),
        Index("ix_documents_expires_at_id", "expires_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True, server_default=text("uuidv4()")
    )
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="images")
    order_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    upload_key_id: Mapped[str | None] = mapped_column(String(32))
    upload_idempotency_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    request_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP + INTERVAL '24 hours'"),
    )


class DocumentCapabilityRecord(Base):
    __tablename__ = "document_capabilities"
    __table_args__ = (
        UniqueConstraint("key_id", "digest"),
        CheckConstraint(
            "scope IN ('read:document','read:document-image','reprocess:document','manage:document')",
            name="scope",
        ),
        CheckConstraint("octet_length(digest) = 32", name="digest_length"),
        Index("ix_document_capabilities_document_id_scope", "document_id", "scope"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.documents.id", ondelete="CASCADE"), nullable=False
    )
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentRetryRequestRecord(Base):
    __tablename__ = "document_retry_requests"
    __table_args__ = (
        UniqueConstraint("document_id", "idempotency_digest"),
        CheckConstraint("octet_length(idempotency_digest) = 32", name="idempotency_length"),
        Index("ix_document_retry_requests_document_id", "document_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.documents.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
