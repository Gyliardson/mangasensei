"""Result-scoped localized JMdict projections over canonical lexical matches."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from mangasensei.infrastructure.database.base import Base


class DictionaryProjectionRequestRecord(Base):
    """Durable input for one dictionary-only reprojection job."""

    __tablename__ = "dictionary_projection_requests"
    __table_args__ = (
        CheckConstraint(
            "requested_dictionary_language IN ('en','de','pt-BR')",
            name="requested_dictionary_language",
        ),
    )

    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mangasensei.jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    requested_dictionary_language: Mapped[str] = mapped_column(String(8), nullable=False)


class DictionaryProjectionRecord(Base):
    """Immutable requested/fallback policy for one completed projection."""

    __tablename__ = "dictionary_projections"
    __table_args__ = (
        CheckConstraint(
            "requested_dictionary_language IN ('en','de','pt-BR')",
            name="requested_dictionary_language",
        ),
        CheckConstraint(
            "fallback_dictionary_language = 'en'",
            name="fallback_dictionary_language",
        ),
        Index("ix_dictionary_projections_linguistic_run_id_job_id", "linguistic_run_id", "job_id"),
    )

    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mangasensei.jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    linguistic_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mangasensei.linguistic_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_dictionary_language: Mapped[str] = mapped_column(String(8), nullable=False)
    fallback_dictionary_language: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DictionaryProjectionSourceRecord(Base):
    """Normalized provenance shared by projected vocabulary items."""

    __tablename__ = "dictionary_projection_sources"
    __table_args__ = (
        CheckConstraint("dataset = 'JMdict'", name="dataset"),
        CheckConstraint("product_language IN ('en','de')", name="product_language"),
        CheckConstraint("octet_length(normalized_digest) = 32", name="normalized_digest_length"),
    )

    projection_job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mangasensei.dictionary_projections.job_id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_ref: Mapped[str] = mapped_column(String(192), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(32), nullable=False)
    product_language: Mapped[str] = mapped_column(String(8), nullable=False)
    source_version: Mapped[str] = mapped_column(String(128), nullable=False)
    normalized_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)


class DictionaryProjectionItemRecord(Base):
    """Localized meanings and fallback semantics for one canonical lexical match."""

    __tablename__ = "dictionary_projection_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_job_id", "source_ref"],
            [
                "mangasensei.dictionary_projection_sources.projection_job_id",
                "mangasensei.dictionary_projection_sources.source_ref",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "effective_dictionary_language IN ('en','de')",
            name="effective_dictionary_language",
        ),
        CheckConstraint(
            "(fallback_used AND fallback_reason IS NOT NULL) OR "
            "(NOT fallback_used AND fallback_reason IS NULL)",
            name="fallback_reason_consistency",
        ),
    )

    projection_job_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lexical_match_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("mangasensei.lexical_matches.id", ondelete="CASCADE"),
        primary_key=True,
    )
    effective_dictionary_language: Mapped[str] = mapped_column(String(8), nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ref: Mapped[str] = mapped_column(String(192), nullable=False)


class DictionaryProjectionMeaningRecord(Base):
    """Ordered projected meanings without duplicating source metadata."""

    __tablename__ = "dictionary_projection_meanings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_job_id", "lexical_match_id"],
            [
                "mangasensei.dictionary_projection_items.projection_job_id",
                "mangasensei.dictionary_projection_items.lexical_match_id",
            ],
            ondelete="CASCADE",
        ),
        CheckConstraint("meaning_ordinal >= 0", name="meaning_ordinal_nonnegative"),
    )

    projection_job_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lexical_match_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meaning_ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)
