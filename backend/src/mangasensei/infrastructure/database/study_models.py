"""Language-aware presentation results over reusable Japanese analysis runs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from mangasensei.infrastructure.database.base import Base


class StudyResultRecord(Base):
    __tablename__ = "study_results"
    __table_args__ = (
        UniqueConstraint("job_id"),
        CheckConstraint("content_language = 'ja'", name="content_language"),
        CheckConstraint("study_language IN ('pt-BR','en')", name="study_language"),
        CheckConstraint("dictionary_language = 'en'", name="dictionary_language"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.jobs.id", ondelete="CASCADE"), nullable=False
    )
    linguistic_run_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.linguistic_runs.id", ondelete="CASCADE"), nullable=False
    )
    content_language: Mapped[str] = mapped_column(String(8), nullable=False)
    study_language: Mapped[str] = mapped_column(String(8), nullable=False)
    dictionary_language: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
