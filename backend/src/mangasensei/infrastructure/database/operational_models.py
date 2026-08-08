"""Persistence records for shared operational controls."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from mangasensei.infrastructure.database.base import Base


class RateLimitBucketRecord(Base):
    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        CheckConstraint("octet_length(key_digest) = 32", name="key_digest_length"),
        CheckConstraint("request_count > 0", name="request_count_positive"),
        Index("ix_rate_limit_buckets_window_start", "window_start"),
    )

    key_digest: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    action: Mapped[str] = mapped_column(String(32), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    request_count: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
