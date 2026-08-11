"""add document recovery controls

Revision ID: f6a3c2d91b47
Revises: e2f6a0c84b11
Create Date: 2026-08-11 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a3c2d91b47"
down_revision: str | None = "e2f6a0c84b11"
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
    (OLD.status = 'pending' AND NEW.status IN ('claimed','cancelled','expired')) OR
    (OLD.status = 'retryable_failure' AND NEW.status IN ('claimed','failed','cancelled','expired')) OR
    (OLD.status = 'claimed' AND NEW.status IN ('processing_ocr','retryable_failure','failed','cancelled','expired')) OR
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
    (OLD.status = 'processing_ocr' AND NEW.status IN ('processing_linguistics','retryable_failure','failed','cancelled','expired')) OR
    (OLD.status = 'processing_linguistics' AND NEW.status IN ('processing_gemini','completed','retryable_failure','failed','cancelled','expired')) OR
    (OLD.status = 'processing_gemini' AND NEW.status IN ('completed','retryable_failure','failed','cancelled','expired')) OR
    (OLD.status IN ('completed','failed','cancelled') AND NEW.status = 'expired')
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


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_document_capabilities_scope"),
        "document_capabilities",
        schema="mangasensei",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_document_capabilities_scope"),
        "document_capabilities",
        "scope IN ('read:document','read:document-image','reprocess:document','manage:document')",
        schema="mangasensei",
    )

    op.create_table(
        "document_retry_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(idempotency_digest) = 32",
            name=op.f("ck_document_retry_requests_idempotency_length"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["mangasensei.documents.id"],
            name=op.f("fk_document_retry_requests_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_retry_requests")),
        sa.UniqueConstraint(
            "document_id",
            "idempotency_digest",
            name=op.f("uq_document_retry_requests_document_id_idempotency_digest"),
        ),
        schema="mangasensei",
    )
    op.create_index(
        "ix_document_retry_requests_document_id",
        "document_retry_requests",
        ["document_id", "id"],
        unique=False,
        schema="mangasensei",
    )

    op.add_column(
        "jobs",
        sa.Column("document_retry_request_id", sa.BigInteger(), nullable=True),
        schema="mangasensei",
    )
    op.create_foreign_key(
        op.f("fk_jobs_document_retry_request_id_document_retry_requests"),
        "jobs",
        "document_retry_requests",
        ["document_retry_request_id"],
        ["id"],
        source_schema="mangasensei",
        referent_schema="mangasensei",
        ondelete="SET NULL",
    )
    op.add_column(
        "jobs",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        schema="mangasensei",
    )
    op.drop_constraint(
        op.f("ck_jobs_terminal_timestamp"),
        "jobs",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_jobs_status"),
        "jobs",
        schema="mangasensei",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_jobs_status"),
        "jobs",
        "status IN ('pending','claimed','processing_ocr','processing_linguistics','processing_gemini','completed','retryable_failure','failed','cancelled','expired')",
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_jobs_terminal_timestamp"),
        "jobs",
        "((status IN ('completed','failed','cancelled','expired') AND finished_at IS NOT NULL) OR "
        "(status NOT IN ('completed','failed','cancelled','expired') AND finished_at IS NULL))",
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_jobs_cancel_request_state"),
        "jobs",
        "cancel_requested_at IS NULL OR status IN "
        "('claimed','processing_ocr','processing_linguistics','processing_gemini','cancelled','expired')",
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_jobs_cancelled_requires_request"),
        "jobs",
        "status != 'cancelled' OR cancel_requested_at IS NOT NULL",
        schema="mangasensei",
    )
    op.execute(_CURRENT_TRANSITION_FUNCTION)


def downgrade() -> None:
    bind = op.get_bind()
    incompatible = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM mangasensei.jobs "
            "WHERE status = 'cancelled' OR cancel_requested_at IS NOT NULL "
            "OR document_retry_request_id IS NOT NULL"
            ") OR EXISTS ("
            "SELECT 1 FROM mangasensei.document_retry_requests"
            ") OR EXISTS ("
            "SELECT 1 FROM mangasensei.document_capabilities WHERE scope = 'manage:document'"
            ")"
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError(
            "cannot safely downgrade Slice C while cancellation, retry, or manage-capability state exists"
        )

    op.execute(_PREVIOUS_TRANSITION_FUNCTION)
    op.drop_constraint(
        op.f("ck_jobs_cancelled_requires_request"),
        "jobs",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_jobs_cancel_request_state"),
        "jobs",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_jobs_terminal_timestamp"),
        "jobs",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_jobs_status"),
        "jobs",
        schema="mangasensei",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_jobs_status"),
        "jobs",
        "status IN ('pending','claimed','processing_ocr','processing_linguistics','processing_gemini','completed','retryable_failure','failed','expired')",
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_jobs_terminal_timestamp"),
        "jobs",
        "((status IN ('completed','failed','expired') AND finished_at IS NOT NULL) OR "
        "(status NOT IN ('completed','failed','expired') AND finished_at IS NULL))",
        schema="mangasensei",
    )
    op.drop_column("jobs", "cancel_requested_at", schema="mangasensei")
    op.drop_constraint(
        op.f("fk_jobs_document_retry_request_id_document_retry_requests"),
        "jobs",
        schema="mangasensei",
        type_="foreignkey",
    )
    op.drop_column("jobs", "document_retry_request_id", schema="mangasensei")
    op.drop_index(
        "ix_document_retry_requests_document_id",
        table_name="document_retry_requests",
        schema="mangasensei",
    )
    op.drop_table("document_retry_requests", schema="mangasensei")

    op.drop_constraint(
        op.f("ck_document_capabilities_scope"),
        "document_capabilities",
        schema="mangasensei",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_document_capabilities_scope"),
        "document_capabilities",
        "scope IN ('read:document','read:document-image','reprocess:document')",
        schema="mangasensei",
    )