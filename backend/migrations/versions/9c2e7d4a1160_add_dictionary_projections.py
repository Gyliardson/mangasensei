"""add localized dictionary projection persistence

Revision ID: 9c2e7d4a1160
Revises: 4b913c2a7e56
Create Date: 2026-08-10 11:55:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c2e7d4a1160"
down_revision: str | None = "4b913c2a7e56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CURRENT_TRANSITION_FUNCTION = """
CREATE OR REPLACE FUNCTION mangasensei.enforce_job_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = NEW.status THEN
    IF NEW.fencing_token <> OLD.fencing_token OR NEW.attempt_count <> OLD.attempt_count THEN
      RAISE EXCEPTION 'fencing and attempt counters may only change during claim';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT (
    (OLD.status = 'pending' AND NEW.status IN ('claimed','expired')) OR
    (OLD.status = 'retryable_failure' AND NEW.status IN ('claimed','failed','expired')) OR
    (OLD.status = 'claimed' AND NEW.status IN ('processing_ocr','retryable_failure','failed','expired')) OR
    (
      OLD.status = 'claimed' AND
      OLD.job_kind = 'study_language_reprocess' AND
      NEW.job_kind = OLD.job_kind AND
      NEW.status IN ('processing_gemini','completed')
    ) OR
    (
      OLD.status = 'claimed' AND
      OLD.job_kind = 'dictionary_language_reprocess' AND
      NEW.job_kind = OLD.job_kind AND
      NEW.status = 'completed'
    ) OR
    (OLD.status = 'processing_ocr' AND NEW.status IN ('processing_linguistics','retryable_failure','failed','expired')) OR
    (OLD.status = 'processing_linguistics' AND NEW.status IN ('processing_gemini','completed','retryable_failure','failed','expired')) OR
    (OLD.status = 'processing_gemini' AND NEW.status IN ('completed','retryable_failure','failed','expired')) OR
    (OLD.status IN ('completed','failed') AND NEW.status = 'expired')
  ) THEN
    RAISE EXCEPTION 'invalid job transition: % -> %', OLD.status, NEW.status;
  END IF;

  IF NEW.status = 'claimed' THEN
    IF NEW.attempt_count <> OLD.attempt_count + 1 OR NEW.fencing_token <> OLD.fencing_token + 1 THEN
      RAISE EXCEPTION 'claim must increment attempt and fencing counters exactly once';
    END IF;
  ELSIF NEW.fencing_token <> OLD.fencing_token OR NEW.attempt_count <> OLD.attempt_count THEN
    RAISE EXCEPTION 'non-claim transition changed attempt or fencing counter';
  END IF;
  RETURN NEW;
END;
$$;
"""

_PREVIOUS_TRANSITION_FUNCTION = """
CREATE OR REPLACE FUNCTION mangasensei.enforce_job_transition() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status = NEW.status THEN
    IF NEW.fencing_token <> OLD.fencing_token OR NEW.attempt_count <> OLD.attempt_count THEN
      RAISE EXCEPTION 'fencing and attempt counters may only change during claim';
    END IF;
    RETURN NEW;
  END IF;

  IF NOT (
    (OLD.status = 'pending' AND NEW.status IN ('claimed','expired')) OR
    (OLD.status = 'retryable_failure' AND NEW.status IN ('claimed','failed','expired')) OR
    (OLD.status = 'claimed' AND NEW.status IN ('processing_ocr','retryable_failure','failed','expired')) OR
    (
      OLD.status = 'claimed' AND
      OLD.job_kind = 'study_language_reprocess' AND
      NEW.job_kind = OLD.job_kind AND
      NEW.status IN ('processing_gemini','completed')
    ) OR
    (OLD.status = 'processing_ocr' AND NEW.status IN ('processing_linguistics','retryable_failure','failed','expired')) OR
    (OLD.status = 'processing_linguistics' AND NEW.status IN ('processing_gemini','completed','retryable_failure','failed','expired')) OR
    (OLD.status = 'processing_gemini' AND NEW.status IN ('completed','retryable_failure','failed','expired')) OR
    (OLD.status IN ('completed','failed') AND NEW.status = 'expired')
  ) THEN
    RAISE EXCEPTION 'invalid job transition: % -> %', OLD.status, NEW.status;
  END IF;

  IF NEW.status = 'claimed' THEN
    IF NEW.attempt_count <> OLD.attempt_count + 1 OR NEW.fencing_token <> OLD.fencing_token + 1 THEN
      RAISE EXCEPTION 'claim must increment attempt and fencing counters exactly once';
    END IF;
  ELSIF NEW.fencing_token <> OLD.fencing_token OR NEW.attempt_count <> OLD.attempt_count THEN
    RAISE EXCEPTION 'non-claim transition changed attempt or fencing counter';
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "dictionary_projection_requests",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_dictionary_language", sa.String(length=8), nullable=False),
        sa.CheckConstraint(
            "requested_dictionary_language IN ('en','de','pt-BR')",
            name=op.f("ck_dictionary_projection_requests_requested_dictionary_language"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["mangasensei.jobs.id"],
            name=op.f("fk_dictionary_projection_requests_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_dictionary_projection_requests")),
        schema="mangasensei",
    )
    op.create_table(
        "dictionary_projections",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("linguistic_run_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_dictionary_language", sa.String(length=8), nullable=False),
        sa.Column("fallback_dictionary_language", sa.String(length=8), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "requested_dictionary_language IN ('en','de','pt-BR')",
            name=op.f("ck_dictionary_projections_requested_dictionary_language"),
        ),
        sa.CheckConstraint(
            "fallback_dictionary_language = 'en'",
            name=op.f("ck_dictionary_projections_fallback_dictionary_language"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["mangasensei.jobs.id"],
            name=op.f("fk_dictionary_projections_job_id_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linguistic_run_id"],
            ["mangasensei.linguistic_runs.id"],
            name=op.f("fk_dictionary_projections_linguistic_run_id_linguistic_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_dictionary_projections")),
        schema="mangasensei",
    )
    op.create_index(
        "ix_dictionary_projections_linguistic_run_id_job_id",
        "dictionary_projections",
        ["linguistic_run_id", "job_id"],
        unique=False,
        schema="mangasensei",
    )
    op.create_table(
        "dictionary_projection_sources",
        sa.Column("projection_job_id", sa.BigInteger(), nullable=False),
        sa.Column("source_ref", sa.String(length=192), nullable=False),
        sa.Column("dataset", sa.String(length=32), nullable=False),
        sa.Column("product_language", sa.String(length=8), nullable=False),
        sa.Column("source_version", sa.String(length=128), nullable=False),
        sa.Column("normalized_digest", sa.LargeBinary(length=32), nullable=False),
        sa.CheckConstraint("dataset = 'JMdict'", name=op.f("ck_dictionary_projection_sources_dataset")),
        sa.CheckConstraint(
            "product_language IN ('en','de')",
            name=op.f("ck_dictionary_projection_sources_product_language"),
        ),
        sa.CheckConstraint(
            "octet_length(normalized_digest) = 32",
            name=op.f("ck_dictionary_projection_sources_normalized_digest_length"),
        ),
        sa.ForeignKeyConstraint(
            ["projection_job_id"],
            ["mangasensei.dictionary_projections.job_id"],
            name=op.f("fk_dictionary_projection_sources_projection_job_id_dictionary_projections"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "projection_job_id",
            "source_ref",
            name=op.f("pk_dictionary_projection_sources"),
        ),
        schema="mangasensei",
    )
    op.create_table(
        "dictionary_projection_items",
        sa.Column("projection_job_id", sa.BigInteger(), nullable=False),
        sa.Column("lexical_match_id", sa.BigInteger(), nullable=False),
        sa.Column("effective_dictionary_language", sa.String(length=8), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("fallback_reason", sa.String(length=64), nullable=True),
        sa.Column("source_ref", sa.String(length=192), nullable=False),
        sa.CheckConstraint(
            "effective_dictionary_language IN ('en','de')",
            name=op.f("ck_dictionary_projection_items_effective_dictionary_language"),
        ),
        sa.CheckConstraint(
            "(fallback_used AND fallback_reason IS NOT NULL) OR "
            "(NOT fallback_used AND fallback_reason IS NULL)",
            name=op.f("ck_dictionary_projection_items_fallback_reason_consistency"),
        ),
        sa.ForeignKeyConstraint(
            ["lexical_match_id"],
            ["mangasensei.lexical_matches.id"],
            name=op.f("fk_dictionary_projection_items_lexical_match_id_lexical_matches"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["projection_job_id", "source_ref"],
            [
                "mangasensei.dictionary_projection_sources.projection_job_id",
                "mangasensei.dictionary_projection_sources.source_ref",
            ],
            name="fk_dictionary_projection_items_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "projection_job_id",
            "lexical_match_id",
            name=op.f("pk_dictionary_projection_items"),
        ),
        schema="mangasensei",
    )
    op.create_table(
        "dictionary_projection_meanings",
        sa.Column("projection_job_id", sa.BigInteger(), nullable=False),
        sa.Column("lexical_match_id", sa.BigInteger(), nullable=False),
        sa.Column("meaning_ordinal", sa.Integer(), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "meaning_ordinal >= 0",
            name=op.f("ck_dictionary_projection_meanings_meaning_ordinal_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["projection_job_id", "lexical_match_id"],
            [
                "mangasensei.dictionary_projection_items.projection_job_id",
                "mangasensei.dictionary_projection_items.lexical_match_id",
            ],
            name="fk_dictionary_projection_meanings_item",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "projection_job_id",
            "lexical_match_id",
            "meaning_ordinal",
            name=op.f("pk_dictionary_projection_meanings"),
        ),
        schema="mangasensei",
    )

    op.execute(
        """
        INSERT INTO mangasensei.dictionary_projections (
            job_id, linguistic_run_id, requested_dictionary_language,
            fallback_dictionary_language, created_at
        )
        SELECT job_id, linguistic_run_id, 'en', 'en', created_at
        FROM mangasensei.study_results
        """
    )
    op.execute(
        """
        INSERT INTO mangasensei.dictionary_projection_sources (
            projection_job_id, source_ref, dataset, product_language,
            source_version, normalized_digest
        )
        SELECT
            projection.job_id,
            concat(
                'jmdict:en:', run.dictionary_version, ':',
                left(encode(run.dictionary_digest, 'hex'), 16)
            ),
            'JMdict',
            'en',
            run.dictionary_version,
            run.dictionary_digest
        FROM mangasensei.dictionary_projections AS projection
        JOIN mangasensei.linguistic_runs AS run
          ON run.id = projection.linguistic_run_id
        """
    )
    op.execute(
        """
        INSERT INTO mangasensei.dictionary_projection_items (
            projection_job_id, lexical_match_id, effective_dictionary_language,
            fallback_used, fallback_reason, source_ref
        )
        SELECT
            projection.job_id,
            lexical.id,
            'en',
            false,
            NULL,
            concat(
                'jmdict:en:', run.dictionary_version, ':',
                left(encode(run.dictionary_digest, 'hex'), 16)
            )
        FROM mangasensei.dictionary_projections AS projection
        JOIN mangasensei.linguistic_runs AS run
          ON run.id = projection.linguistic_run_id
        JOIN mangasensei.lexical_matches AS lexical
          ON lexical.linguistic_run_id = projection.linguistic_run_id
        """
    )
    op.execute(
        """
        INSERT INTO mangasensei.dictionary_projection_meanings (
            projection_job_id, lexical_match_id, meaning_ordinal, meaning
        )
        SELECT
            item.projection_job_id,
            meaning.lexical_match_id,
            meaning.meaning_ordinal,
            meaning.meaning
        FROM mangasensei.dictionary_projection_items AS item
        JOIN mangasensei.lexical_meanings AS meaning
          ON meaning.lexical_match_id = item.lexical_match_id
        """
    )
    op.execute(_CURRENT_TRANSITION_FUNCTION)


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM mangasensei.dictionary_projection_requests
            WHERE requested_dictionary_language <> 'en'
          ) OR EXISTS (
            SELECT 1
            FROM mangasensei.dictionary_projections
            WHERE requested_dictionary_language <> 'en'
               OR fallback_dictionary_language <> 'en'
          ) OR EXISTS (
            SELECT 1
            FROM mangasensei.dictionary_projection_items
            WHERE effective_dictionary_language <> 'en'
               OR fallback_used
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade while multilingual dictionary projection state exists';
          END IF;
        END
        $$;
        """
    )
    op.execute(_PREVIOUS_TRANSITION_FUNCTION)
    op.drop_table("dictionary_projection_meanings", schema="mangasensei")
    op.drop_table("dictionary_projection_items", schema="mangasensei")
    op.drop_table("dictionary_projection_sources", schema="mangasensei")
    op.drop_index(
        "ix_dictionary_projections_linguistic_run_id_job_id",
        table_name="dictionary_projections",
        schema="mangasensei",
    )
    op.drop_table("dictionary_projections", schema="mangasensei")
    op.drop_table("dictionary_projection_requests", schema="mangasensei")
