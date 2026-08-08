"""add rate limit buckets

Revision ID: a17e52c4d908
Revises: f41c7a90b2de
Create Date: 2026-08-09 00:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a17e52c4d908"
down_revision: str | None = "f41c7a90b2de"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(key_digest) = 32",
            name=op.f("ck_rate_limit_buckets_key_digest_length"),
        ),
        sa.CheckConstraint(
            "request_count > 0",
            name=op.f("ck_rate_limit_buckets_request_count_positive"),
        ),
        sa.PrimaryKeyConstraint(
            "key_digest",
            "action",
            "window_start",
            name=op.f("pk_rate_limit_buckets"),
        ),
        schema="mangasensei",
    )
    op.create_index(
        "ix_rate_limit_buckets_window_start",
        "rate_limit_buckets",
        ["window_start"],
        unique=False,
        schema="mangasensei",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rate_limit_buckets_window_start",
        table_name="rate_limit_buckets",
        schema="mangasensei",
    )
    op.drop_table("rate_limit_buckets", schema="mangasensei")
