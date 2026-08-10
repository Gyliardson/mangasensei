"""Persistence records for immutable blobs, pages and capabilities."""

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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mangasensei.infrastructure.database.base import Base


class ImageBlobRecord(Base):
    __tablename__ = "image_blobs"
    __table_args__ = (
        CheckConstraint("octet_length(sha256) = 32", name="sha256_length"),
        CheckConstraint("byte_size > 0 AND byte_size <= 12582912", name="byte_size_range"),
        CheckConstraint("width > 0 AND width <= 10000", name="width_range"),
        CheckConstraint("height > 0 AND height <= 10000", name="height_range"),
        CheckConstraint("width * height <= 25000000", name="pixel_limit"),
        CheckConstraint("media_type IN ('image/jpeg','image/png','image/webp')", name="media_type"),
        CheckConstraint("state IN ('ready','deleting')", name="state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(nullable=False)
    height: Mapped[int] = mapped_column(nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PageRecord(Base):
    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("upload_key_id", "upload_idempotency_digest"),
        UniqueConstraint("document_id", "ordinal"),
        CheckConstraint(
            "upload_idempotency_digest IS NULL OR octet_length(upload_idempotency_digest) = 32",
            name="idempotency_digest_length",
        ),
        CheckConstraint("octet_length(request_digest) = 32", name="request_digest_length"),
        CheckConstraint("expires_at = created_at + interval '24 hours'", name="retention_exact"),
        CheckConstraint(
            "(document_id IS NULL AND ordinal IS NULL) OR "
            "(document_id IS NOT NULL AND ordinal IS NOT NULL)",
            name="document_membership_pair",
        ),
        CheckConstraint(
            "(document_id IS NULL AND upload_key_id IS NOT NULL "
            "AND upload_idempotency_digest IS NOT NULL) OR "
            "(document_id IS NOT NULL AND upload_key_id IS NULL "
            "AND upload_idempotency_digest IS NULL)",
            name="standalone_idempotency_pair",
        ),
        CheckConstraint("ordinal IS NULL OR ordinal >= 0", name="ordinal_nonnegative"),
        Index("ix_pages_expires_at_id", "expires_at", "id"),
        Index("ix_pages_image_blob_id", "image_blob_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True, server_default=text("uuidv4()")
    )
    image_blob_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.image_blobs.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("mangasensei.documents.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int | None] = mapped_column()
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    upload_key_id: Mapped[str | None] = mapped_column(String(32))
    upload_idempotency_digest: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    request_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP + INTERVAL '24 hours'"),
    )


class PageCapabilityRecord(Base):
    __tablename__ = "page_capabilities"
    __table_args__ = (
        UniqueConstraint("key_id", "digest"),
        CheckConstraint("scope IN ('read:page','read:image','reprocess:page')", name="scope"),
        CheckConstraint("octet_length(digest) = 32", name="digest_length"),
        Index("ix_page_capabilities_page_id_scope", "page_id", "scope"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.pages.id", ondelete="CASCADE"), nullable=False
    )
    key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
