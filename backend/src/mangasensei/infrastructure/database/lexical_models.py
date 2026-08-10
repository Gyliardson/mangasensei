"""Persistence records for resolved language-neutral lexical matches."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from mangasensei.infrastructure.database.base import Base


class LexicalMatchRecord(Base):
    __tablename__ = "lexical_matches"
    __table_args__ = (
        UniqueConstraint("linguistic_run_id", "stable_key"),
        UniqueConstraint(
            "linguistic_run_id",
            "region_id",
            "start_token_ordinal",
            "end_token_ordinal",
            "dictionary_namespace",
            "dictionary_entry_id",
            "form_lemma",
            "form_reading",
            name="uq_lexical_matches_occurrence_identity",
        ),
        CheckConstraint("octet_length(stable_key) = 32", name="stable_key_length"),
        CheckConstraint(
            "start_token_ordinal >= 0 AND end_token_ordinal > start_token_ordinal",
            name="token_span",
        ),
        CheckConstraint(
            "char_length(dictionary_namespace) BETWEEN 1 AND 32",
            name="dictionary_namespace_length",
        ),
        CheckConstraint(
            "jlpt_level IS NULL OR jlpt_level IN ('N1','N2','N3','N4','N5')",
            name="jlpt",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    linguistic_run_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.linguistic_runs.id", ondelete="CASCADE"), nullable=False
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.ocr_regions.id", ondelete="CASCADE"), nullable=False
    )
    stable_key: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    start_token_ordinal: Mapped[int] = mapped_column(nullable=False)
    end_token_ordinal: Mapped[int] = mapped_column(nullable=False)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    display_lemma: Mapped[str] = mapped_column(Text, nullable=False)
    display_reading: Mapped[str] = mapped_column(Text, nullable=False)
    dictionary_namespace: Mapped[str] = mapped_column(String(32), nullable=False)
    dictionary_entry_id: Mapped[str] = mapped_column(String(128), nullable=False)
    form_lemma: Mapped[str] = mapped_column(Text, nullable=False)
    form_reading: Mapped[str] = mapped_column(Text, nullable=False)
    dictionary_source: Mapped[str] = mapped_column(String(64), nullable=False)
    jlpt_level: Mapped[str | None] = mapped_column(String(2))
    jlpt_official: Mapped[bool] = mapped_column(nullable=False)


class LexicalMeaningRecord(Base):
    __tablename__ = "lexical_meanings"

    lexical_match_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.lexical_matches.id", ondelete="CASCADE"), primary_key=True
    )
    meaning_ordinal: Mapped[int] = mapped_column(primary_key=True)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)


class GeminiLexicalVocabularyLinkRecord(Base):
    __tablename__ = "gemini_lexical_vocabulary_links"

    region_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.gemini_region_analyses.id", ondelete="CASCADE"), primary_key=True
    )
    lexical_match_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.lexical_matches.id", ondelete="CASCADE"), primary_key=True
    )
