"""add document aggregate persistence

Revision ID: b7d2f4a91c63
Revises: e63b0c4d129a
Create Date: 2026-08-09 21:35:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d2f4a91c63"
down_revision: str | None = "e63b0c4d129a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "public_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuidv4()"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=16), server_default="images", nullable=False),
        sa.Column("order_revision", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP + INTERVAL '24 hours'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_kind IN ('images')",
            name=op.f("ck_documents_source_kind"),
        ),
        sa.CheckConstraint(
            "order_revision > 0",
            name=op.f("ck_documents_order_revision_positive"),
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '24 hours'",
            name=op.f("ck_documents_retention_exact"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("public_id", name=op.f("uq_documents_public_id")),
        schema="mangasensei",
    )
    op.create_index(
        "ix_documents_expires_at_id",
        "documents",
        ["expires_at", "id"],
        unique=False,
        schema="mangasensei",
    )
    op.create_table(
        "document_capabilities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("key_id", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope IN ('read:document','read:document-image')",
            name=op.f("ck_document_capabilities_scope"),
        ),
        sa.CheckConstraint(
            "octet_length(digest) = 32",
            name=op.f("ck_document_capabilities_digest_length"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["mangasensei.documents.id"],
            name=op.f("fk_document_capabilities_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_capabilities")),
        sa.UniqueConstraint(
            "key_id",
            "digest",
            name=op.f("uq_document_capabilities_key_id_digest"),
        ),
        schema="mangasensei",
    )
    op.create_index(
        "ix_document_capabilities_document_id_scope",
        "document_capabilities",
        ["document_id", "scope"],
        unique=False,
        schema="mangasensei",
    )

    op.add_column(
        "pages",
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        schema="mangasensei",
    )
    op.add_column(
        "pages",
        sa.Column("ordinal", sa.Integer(), nullable=True),
        schema="mangasensei",
    )
    op.create_foreign_key(
        op.f("fk_pages_document_id_documents"),
        "pages",
        "documents",
        ["document_id"],
        ["id"],
        source_schema="mangasensei",
        referent_schema="mangasensei",
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_pages_document_membership_pair"),
        "pages",
        "(document_id IS NULL AND ordinal IS NULL) OR "
        "(document_id IS NOT NULL AND ordinal IS NOT NULL)",
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_pages_ordinal_nonnegative"),
        "pages",
        "ordinal IS NULL OR ordinal >= 0",
        schema="mangasensei",
    )
    op.create_unique_constraint(
        op.f("uq_pages_document_id_ordinal"),
        "pages",
        ["document_id", "ordinal"],
        schema="mangasensei",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_pages_document_id_ordinal"),
        "pages",
        schema="mangasensei",
        type_="unique",
    )
    op.drop_constraint(
        op.f("ck_pages_ordinal_nonnegative"),
        "pages",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_pages_document_membership_pair"),
        "pages",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_pages_document_id_documents"),
        "pages",
        schema="mangasensei",
        type_="foreignkey",
    )
    op.drop_column("pages", "ordinal", schema="mangasensei")
    op.drop_column("pages", "document_id", schema="mangasensei")
    op.drop_index(
        "ix_document_capabilities_document_id_scope",
        table_name="document_capabilities",
        schema="mangasensei",
    )
    op.drop_table("document_capabilities", schema="mangasensei")
    op.drop_index(
        "ix_documents_expires_at_id",
        table_name="documents",
        schema="mangasensei",
    )
    op.drop_table("documents", schema="mangasensei")
