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
        CheckConstraint("source_kind IN ('images')", name="source_kind"),
        CheckConstraint("order_revision > 0", name="order_revision_positive"),
        CheckConstraint("expires_at = created_at + interval '24 hours'", name="retention_exact"),
        Index("ix_documents_expires_at_id", "expires_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True, server_default=text("uuidv4()")
    )
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default="images")
    order_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
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
            "scope IN ('read:document','read:document-image')",
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
