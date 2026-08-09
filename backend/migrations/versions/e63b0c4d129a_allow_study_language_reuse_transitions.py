"""allow study-language reuse job transitions

Revision ID: e63b0c4d129a
Revises: c63a9b14e2f0
Create Date: 2026-08-09 08:33:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e63b0c4d129a"
down_revision: str | None = "c63a9b14e2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CURRENT_FUNCTION = """
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

_PREVIOUS_FUNCTION = """
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
    op.execute(_CURRENT_FUNCTION)


def downgrade() -> None:
    op.execute(_PREVIOUS_FUNCTION)
