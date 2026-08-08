"""Durable PostgreSQL queue projection and attempt audit."""

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

ACTIVE_STATUSES_SQL = "'claimed','processing_ocr','processing_linguistics','processing_gemini'"
ALL_STATUSES_SQL = (
    "'pending','claimed','processing_ocr','processing_linguistics','processing_gemini',"
    "'completed','retryable_failure','failed','expired'"
)
TERMINAL_STATUSES_SQL = "'completed','failed','expired'"


class JobRecord(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("page_id", "job_kind", "idempotency_digest"),
        CheckConstraint(f"status IN ({ALL_STATUSES_SQL})", name="status"),
        CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= max_attempts", name="attempt_range"
        ),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint("fencing_token >= 0", name="fencing_nonnegative"),
        CheckConstraint("octet_length(idempotency_digest) = 32", name="idempotency_length"),
        CheckConstraint("octet_length(request_digest) = 32", name="request_digest_length"),
        CheckConstraint(
            f"((status IN ({ACTIVE_STATUSES_SQL}) AND worker_id IS NOT NULL AND "
            "lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL) OR "
            f"(status NOT IN ({ACTIVE_STATUSES_SQL}) AND worker_id IS NULL AND "
            "lease_expires_at IS NULL AND heartbeat_at IS NULL))",
            name="lease_ownership",
        ),
        CheckConstraint(
            f"((status IN ({TERMINAL_STATUSES_SQL}) AND finished_at IS NOT NULL) OR "
            f"(status NOT IN ({TERMINAL_STATUSES_SQL}) AND finished_at IS NULL))",
            name="terminal_timestamp",
        ),
        Index(
            "ix_jobs_claim",
            "available_at",
            "id",
            postgresql_where=text("status IN ('pending','retryable_failure')"),
        ),
        Index(
            "ix_jobs_recovery",
            "lease_expires_at",
            "id",
            postgresql_where=text(
                "status IN ('claimed','processing_ocr','processing_linguistics',"
                "'processing_gemini')"
            ),
        ),
        Index("ix_jobs_page_created", "page_id", "created_at", "id"),
        Index(
            "uq_jobs_one_active_page",
            "page_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending','claimed','processing_ocr','processing_linguistics',"
                "'processing_gemini','retryable_failure')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True, server_default=text("uuidv4()")
    )
    page_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.pages.id", ondelete="CASCADE"), nullable=False
    )
    job_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="page_analysis"
    )
    idempotency_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    attempt_count: Mapped[int] = mapped_column(nullable=False, server_default="0")
    max_attempts: Mapped[int] = mapped_column(nullable=False, server_default="3")
    worker_id: Mapped[str | None] = mapped_column(String(128))
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pipeline_version: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="0.1.0"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class JobAttemptRecord(Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "fencing_token"),
        CheckConstraint("attempt_no > 0", name="attempt_positive"),
        CheckConstraint("fencing_token > 0", name="fencing_positive"),
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.jobs.id", ondelete="CASCADE"), primary_key=True
    )
    attempt_no: Mapped[int] = mapped_column(primary_key=True)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)
