"""OCR, deterministic linguistic, Gemini analysis and billing records."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mangasensei.infrastructure.database.base import Base


class OcrRunRecord(Base):
    __tablename__ = "ocr_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "fencing_token"),
        CheckConstraint("octet_length(input_sha256) = 32", name="input_sha256_length"),
        CheckConstraint("width > 0 AND height > 0", name="dimensions_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.jobs.id", ondelete="CASCADE"), nullable=False
    )
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    recognizer: Mapped[str] = mapped_column(String(64), nullable=False)
    model_manifest_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    upstream_repository: Mapped[str] = mapped_column(Text, nullable=False)
    upstream_commit: Mapped[str] = mapped_column(String(128), nullable=False)
    input_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    width: Mapped[int] = mapped_column(nullable=False)
    height: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OcrRegionRecord(Base):
    __tablename__ = "ocr_regions"
    __table_args__ = (
        UniqueConstraint("ocr_run_id", "region_ordinal"),
        UniqueConstraint("ocr_run_id", "reading_order"),
        UniqueConstraint("ocr_run_id", "public_id"),
        CheckConstraint("x >= 0 AND y >= 0 AND width > 0 AND height > 0", name="bbox_positive"),
        CheckConstraint("normalized_x >= 0 AND normalized_x <= 1", name="normalized_x"),
        CheckConstraint("normalized_y >= 0 AND normalized_y <= 1", name="normalized_y"),
        CheckConstraint("normalized_width > 0 AND normalized_width <= 1", name="normalized_width"),
        CheckConstraint(
            "normalized_height > 0 AND normalized_height <= 1", name="normalized_height"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        CheckConstraint("angle >= -180 AND angle <= 180", name="angle"),
        CheckConstraint("char_length(raw_text) BETWEEN 1 AND 10000", name="raw_text_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, server_default=text("uuidv4()")
    )
    ocr_run_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.ocr_runs.id", ondelete="CASCADE"), nullable=False
    )
    region_ordinal: Mapped[int] = mapped_column(nullable=False)
    reading_order: Mapped[int] = mapped_column(nullable=False)
    x: Mapped[int] = mapped_column(nullable=False)
    y: Mapped[int] = mapped_column(nullable=False)
    width: Mapped[int] = mapped_column(nullable=False)
    height: Mapped[int] = mapped_column(nullable=False)
    normalized_x: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    normalized_y: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    normalized_width: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    normalized_height: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    angle: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(8, 7), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_text: Mapped[str | None] = mapped_column(Text)


class OcrRegionVertexRecord(Base):
    __tablename__ = "ocr_region_vertices"
    __table_args__ = (CheckConstraint("x >= 0 AND y >= 0", name="coordinates_nonnegative"),)

    region_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.ocr_regions.id", ondelete="CASCADE"), primary_key=True
    )
    vertex_ordinal: Mapped[int] = mapped_column(primary_key=True)
    x: Mapped[int] = mapped_column(nullable=False)
    y: Mapped[int] = mapped_column(nullable=False)


class LinguisticRunRecord(Base):
    __tablename__ = "linguistic_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "fencing_token"),
        CheckConstraint("octet_length(config_digest) = 32", name="config_digest_length"),
        CheckConstraint("octet_length(dictionary_digest) = 32", name="dictionary_digest_length"),
        CheckConstraint("octet_length(input_digest) = 32", name="input_digest_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.jobs.id", ondelete="CASCADE"), nullable=False
    )
    ocr_run_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.ocr_runs.id", ondelete="CASCADE"), nullable=False
    )
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tokenizer_name: Mapped[str] = mapped_column(String(64), nullable=False)
    tokenizer_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    dictionary_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dictionary_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dictionary_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    input_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LinguisticTokenRecord(Base):
    __tablename__ = "linguistic_tokens"
    __table_args__ = (
        UniqueConstraint("region_id", "token_ordinal"),
        UniqueConstraint("linguistic_run_id", "stable_key"),
        CheckConstraint("octet_length(stable_key) = 32", name="stable_key_length"),
        CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="offset_range"),
        CheckConstraint(
            "jlpt_level IS NULL OR jlpt_level IN ('N1','N2','N3','N4','N5')", name="jlpt"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    linguistic_run_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.linguistic_runs.id", ondelete="CASCADE"), nullable=False
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.ocr_regions.id", ondelete="CASCADE"), nullable=False
    )
    token_ordinal: Mapped[int] = mapped_column(nullable=False)
    stable_key: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    start_offset: Mapped[int] = mapped_column(nullable=False)
    end_offset: Mapped[int] = mapped_column(nullable=False)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    lemma: Mapped[str] = mapped_column(Text, nullable=False)
    reading: Mapped[str] = mapped_column(Text, nullable=False)
    part_of_speech: Mapped[str] = mapped_column(Text, nullable=False)
    dictionary_entry_id: Mapped[str | None] = mapped_column(String(128))
    dictionary_source: Mapped[str | None] = mapped_column(String(64))
    jlpt_level: Mapped[str | None] = mapped_column(String(2))
    jlpt_official: Mapped[bool | None]


class LinguisticMeaningRecord(Base):
    __tablename__ = "linguistic_meanings"

    token_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.linguistic_tokens.id", ondelete="CASCADE"), primary_key=True
    )
    meaning_ordinal: Mapped[int] = mapped_column(primary_key=True)
    meaning: Mapped[str] = mapped_column(Text, nullable=False)


class GeminiBudgetBucketRecord(Base):
    __tablename__ = "gemini_budget_buckets"
    __table_args__ = (
        CheckConstraint("reserved_amount >= 0 AND actual_amount >= 0", name="amounts_nonnegative"),
    )

    budget_date: Mapped[date] = mapped_column(Date, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), primary_key=True, server_default="USD")
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    reserved_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default="0"
    )
    actual_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, server_default="0"
    )


class GeminiCallRecord(Base):
    __tablename__ = "gemini_calls"
    __table_args__ = (
        UniqueConstraint("page_id", "page_call_ordinal"),
        CheckConstraint("page_call_ordinal BETWEEN 1 AND 3", name="call_ordinal"),
        CheckConstraint("octet_length(request_digest) = 32", name="request_digest_length"),
        CheckConstraint("reserved_cost >= 0", name="reserved_cost_nonnegative"),
        CheckConstraint(
            "state IN ('reserved','sent','succeeded','failed','unknown')", name="state"
        ),
        Index(
            "uq_gemini_calls_provider_request_id",
            "provider_request_id",
            unique=True,
            postgresql_where=text("provider_request_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True, server_default=text("uuidv4()")
    )
    page_id: Mapped[int | None] = mapped_column(
        ForeignKey("mangasensei.pages.id", ondelete="SET NULL")
    )
    job_id: Mapped[int | None] = mapped_column(
        ForeignKey("mangasensei.jobs.id", ondelete="SET NULL")
    )
    page_call_ordinal: Mapped[int] = mapped_column(nullable=False)
    fencing_token: Mapped[int | None] = mapped_column(BigInteger)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    request_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="reserved")
    reserved_cost: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GeminiAnalysisRecord(Base):
    __tablename__ = "gemini_analyses"
    __table_args__ = (
        UniqueConstraint("job_id"),
        UniqueConstraint("gemini_call_id"),
        CheckConstraint("octet_length(response_digest) = 32", name="response_digest_length"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.jobs.id", ondelete="CASCADE"), nullable=False
    )
    linguistic_run_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.linguistic_runs.id", ondelete="CASCADE"), nullable=False
    )
    gemini_call_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.gemini_calls.id", ondelete="RESTRICT"), nullable=False
    )
    response_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GeminiRegionAnalysisRecord(Base):
    __tablename__ = "gemini_region_analyses"
    __table_args__ = (UniqueConstraint("analysis_id", "region_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.gemini_analyses.id", ondelete="CASCADE"), nullable=False
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.ocr_regions.id", ondelete="CASCADE"), nullable=False
    )
    translation: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class GeminiGrammarPointRecord(Base):
    __tablename__ = "gemini_grammar_points"

    region_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.gemini_region_analyses.id", ondelete="CASCADE"), primary_key=True
    )
    grammar_ordinal: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)


class GeminiVocabularyLinkRecord(Base):
    __tablename__ = "gemini_vocabulary_links"

    region_analysis_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.gemini_region_analyses.id", ondelete="CASCADE"), primary_key=True
    )
    token_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.linguistic_tokens.id", ondelete="CASCADE"), primary_key=True
    )


class GeminiCostLedgerRecord(Base):
    __tablename__ = "gemini_cost_ledger"
    __table_args__ = (
        UniqueConstraint("gemini_call_id", "observation_key"),
        CheckConstraint("token_quantity >= 0", name="token_quantity_nonnegative"),
        CheckConstraint("unit_rate >= 0", name="unit_rate_nonnegative"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gemini_call_id: Mapped[int] = mapped_column(
        ForeignKey("mangasensei.gemini_calls.id", ondelete="CASCADE"), nullable=False
    )
    observation_key: Mapped[str] = mapped_column(String(128), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    usage_category: Mapped[str] = mapped_column(String(32), nullable=False)
    token_quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_rate: Mapped[Decimal] = mapped_column(Numeric(18, 12), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
