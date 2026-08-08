"""scope analysis identities to runs

Revision ID: f41c7a90b2de
Revises: d6e8c4a92710
Create Date: 2026-08-08 23:59:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f41c7a90b2de"
down_revision: str | None = "d6e8c4a92710"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_ocr_regions_public_id",
        "ocr_regions",
        schema="mangasensei",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ocr_regions_ocr_run_id_public_id",
        "ocr_regions",
        ["ocr_run_id", "public_id"],
        schema="mangasensei",
    )
    op.drop_constraint(
        "uq_linguistic_tokens_stable_key",
        "linguistic_tokens",
        schema="mangasensei",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_linguistic_tokens_linguistic_run_id_stable_key",
        "linguistic_tokens",
        ["linguistic_run_id", "stable_key"],
        schema="mangasensei",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_linguistic_tokens_linguistic_run_id_stable_key",
        "linguistic_tokens",
        schema="mangasensei",
        type_="unique",
    )
    op.execute(
        """
        WITH ranked AS (
          SELECT id, row_number() OVER (PARTITION BY stable_key ORDER BY id DESC) AS duplicate_no
          FROM mangasensei.linguistic_tokens
        )
        UPDATE mangasensei.linguistic_tokens AS token
        SET stable_key = decode(
          md5(encode(token.stable_key, 'hex') || ':' || token.id::text) ||
          md5('mangasensei:' || token.id::text),
          'hex'
        )
        FROM ranked
        WHERE token.id = ranked.id AND ranked.duplicate_no > 1
        """
    )
    op.create_unique_constraint(
        "uq_linguistic_tokens_stable_key",
        "linguistic_tokens",
        ["stable_key"],
        schema="mangasensei",
    )
    op.drop_constraint(
        "uq_ocr_regions_ocr_run_id_public_id",
        "ocr_regions",
        schema="mangasensei",
        type_="unique",
    )
    op.execute(
        """
        WITH ranked AS (
          SELECT id, row_number() OVER (PARTITION BY public_id ORDER BY id DESC) AS duplicate_no
          FROM mangasensei.ocr_regions
        )
        UPDATE mangasensei.ocr_regions AS region
        SET public_id = uuidv4()
        FROM ranked
        WHERE region.id = ranked.id AND ranked.duplicate_no > 1
        """
    )
    op.create_unique_constraint(
        "uq_ocr_regions_public_id",
        "ocr_regions",
        ["public_id"],
        schema="mangasensei",
    )
