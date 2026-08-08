"""add integrity triggers

Revision ID: d6e8c4a92710
Revises: b12467733e37
Create Date: 2026-08-07 23:25:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d6e8c4a92710"
down_revision: str | None = "b12467733e37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION mangasensei.enforce_job_transition() RETURNS trigger
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

        CREATE TRIGGER trg_jobs_transition
        BEFORE UPDATE ON mangasensei.jobs
        FOR EACH ROW EXECUTE FUNCTION mangasensei.enforce_job_transition();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mangasensei.enforce_ready_blob() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM mangasensei.image_blobs
            WHERE id = NEW.image_blob_id AND state = 'ready'
            FOR KEY SHARE
          ) THEN
            RAISE EXCEPTION 'page can only attach a ready image blob';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_pages_ready_blob
        BEFORE INSERT OR UPDATE OF image_blob_id ON mangasensei.pages
        FOR EACH ROW EXECUTE FUNCTION mangasensei.enforce_ready_blob();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mangasensei.enforce_capability_expiry() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE page_expiry timestamptz;
        BEGIN
          SELECT expires_at INTO page_expiry FROM mangasensei.pages
          WHERE id = NEW.page_id FOR KEY SHARE;
          IF NEW.expires_at > page_expiry THEN
            RAISE EXCEPTION 'capability cannot outlive its page';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_capabilities_expiry
        BEFORE INSERT OR UPDATE OF page_id, expires_at ON mangasensei.page_capabilities
        FOR EACH ROW EXECUTE FUNCTION mangasensei.enforce_capability_expiry();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mangasensei.enforce_region_bounds() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE run_width integer; run_height integer;
        BEGIN
          SELECT width, height INTO run_width, run_height
          FROM mangasensei.ocr_runs WHERE id = NEW.ocr_run_id FOR KEY SHARE;
          IF NEW.x + NEW.width > run_width OR NEW.y + NEW.height > run_height OR
             NEW.normalized_x + NEW.normalized_width > 1 OR
             NEW.normalized_y + NEW.normalized_height > 1 THEN
            RAISE EXCEPTION 'OCR region is outside page dimensions';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_ocr_regions_bounds
        BEFORE INSERT OR UPDATE ON mangasensei.ocr_regions
        FOR EACH ROW EXECUTE FUNCTION mangasensei.enforce_region_bounds();
        """
    )
    op.execute(
        """
        CREATE FUNCTION mangasensei.enforce_vertex_bounds() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE run_width integer; run_height integer;
        BEGIN
          SELECT r.width, r.height INTO run_width, run_height
          FROM mangasensei.ocr_regions region
          JOIN mangasensei.ocr_runs r ON r.id = region.ocr_run_id
          WHERE region.id = NEW.region_id FOR KEY SHARE OF region, r;
          IF NEW.x > run_width OR NEW.y > run_height THEN
            RAISE EXCEPTION 'OCR polygon vertex is outside page dimensions';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_ocr_vertices_bounds
        BEFORE INSERT OR UPDATE ON mangasensei.ocr_region_vertices
        FOR EACH ROW EXECUTE FUNCTION mangasensei.enforce_vertex_bounds();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_ocr_vertices_bounds ON mangasensei.ocr_region_vertices")
    op.execute("DROP FUNCTION mangasensei.enforce_vertex_bounds()")
    op.execute("DROP TRIGGER trg_ocr_regions_bounds ON mangasensei.ocr_regions")
    op.execute("DROP FUNCTION mangasensei.enforce_region_bounds()")
    op.execute("DROP TRIGGER trg_capabilities_expiry ON mangasensei.page_capabilities")
    op.execute("DROP FUNCTION mangasensei.enforce_capability_expiry()")
    op.execute("DROP TRIGGER trg_pages_ready_blob ON mangasensei.pages")
    op.execute("DROP FUNCTION mangasensei.enforce_ready_blob()")
    op.execute("DROP TRIGGER trg_jobs_transition ON mangasensei.jobs")
    op.execute("DROP FUNCTION mangasensei.enforce_job_transition()")
