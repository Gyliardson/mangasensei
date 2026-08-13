"""add hardened PDF import state

Revision ID: a3f9d712c640
Revises: f6a3c2d91b47
Create Date: 2026-08-11 18:15:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f9d712c640"
down_revision: str | None = "f6a3c2d91b47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ERROR_CODES = (
    "'pdf_invalid','pdf_encrypted_unsupported','pdf_page_limit','pdf_geometry_limit',"
    "'pdf_pixel_limit','pdf_raster_bytes_limit','pdf_renderer_timeout','pdf_renderer_crash',"
    "'pdf_temp_storage_exhausted','pdf_render_failed','pdf_raster_validation_failed',"
    "'pdf_manifest_invalid'"
)


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_documents_source_kind"),
        "documents",
        schema="mangasensei",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_documents_source_kind"),
        "documents",
        "source_kind IN ('images','pdf')",
        schema="mangasensei",
    )

    op.create_table(
        "document_imports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "public_id",
            sa.UUID(),
            server_default=sa.text("uuidv4()"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=16), server_default="pdf", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.LargeBinary(length=32), nullable=False),
        sa.Column("source_bytes", sa.BigInteger(), nullable=False),
        sa.Column("study_language", sa.String(length=8), nullable=False),
        sa.Column("raster_contract", sa.String(length=32), nullable=False),
        sa.Column("upload_key_id", sa.String(length=32), nullable=False),
        sa.Column("upload_idempotency_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("request_digest", sa.LargeBinary(length=32), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("renderer_pypdfium2", sa.String(length=32), nullable=True),
        sa.Column("renderer_pdfium", sa.String(length=64), nullable=True),
        sa.Column("renderer_pillow", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
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
        sa.Column(
            "source_expires_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP + INTERVAL '1 hour'"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_cleaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_kind = 'pdf'", name=op.f("ck_document_imports_source_kind")),
        sa.CheckConstraint(
            "status IN ('queued','rendering','completed','failed')",
            name=op.f("ck_document_imports_status"),
        ),
        sa.CheckConstraint(
            "octet_length(source_sha256) = 32",
            name=op.f("ck_document_imports_source_sha256_length"),
        ),
        sa.CheckConstraint(
            "source_bytes > 0 AND source_bytes <= 268435456",
            name=op.f("ck_document_imports_source_bytes_range"),
        ),
        sa.CheckConstraint(
            "octet_length(upload_idempotency_digest) = 32",
            name=op.f("ck_document_imports_idempotency_length"),
        ),
        sa.CheckConstraint(
            "octet_length(request_digest) = 32",
            name=op.f("ck_document_imports_request_digest_length"),
        ),
        sa.CheckConstraint("fencing_token >= 0", name=op.f("ck_document_imports_fencing_nonnegative")),
        sa.CheckConstraint(
            "page_count IS NULL OR (page_count >= 1 AND page_count <= 200)",
            name=op.f("ck_document_imports_page_count"),
        ),
        sa.CheckConstraint(
            "expires_at = created_at + interval '24 hours'",
            name=op.f("ck_document_imports_retention_exact"),
        ),
        sa.CheckConstraint(
            "source_expires_at = created_at + interval '1 hour'",
            name=op.f("ck_document_imports_source_retention_exact"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND document_id IS NOT NULL AND error_code IS NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status = 'failed' AND document_id IS NULL AND error_code IS NOT NULL "
            "AND finished_at IS NOT NULL) OR "
            "(status IN ('queued','rendering') AND document_id IS NULL "
            "AND error_code IS NULL AND finished_at IS NULL)",
            name=op.f("ck_document_imports_terminal_state"),
        ),
        sa.CheckConstraint(
            "(status = 'rendering' AND lease_owner IS NOT NULL AND lease_until IS NOT NULL) OR "
            "(status != 'rendering' AND lease_owner IS NULL AND lease_until IS NULL)",
            name=op.f("ck_document_imports_lease_state"),
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_ERROR_CODES})",
            name=op.f("ck_document_imports_error_code"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["mangasensei.documents.id"],
            name=op.f("fk_document_imports_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_imports")),
        sa.UniqueConstraint("public_id", name=op.f("uq_document_imports_public_id")),
        sa.UniqueConstraint(
            "upload_key_id",
            "upload_idempotency_digest",
            name=op.f("uq_document_imports_upload_key_id_upload_idempotency_digest"),
        ),
        sa.UniqueConstraint("document_id", name=op.f("uq_document_imports_document_id")),
        schema="mangasensei",
    )
    op.create_index(
        "ix_document_imports_status_lease",
        "document_imports",
        ["status", "lease_until", "id"],
        schema="mangasensei",
    )
    op.create_index(
        "ix_document_imports_source_expiry",
        "document_imports",
        ["source_expires_at", "id"],
        schema="mangasensei",
    )
    op.create_index(
        "ix_document_imports_expires_at_id",
        "document_imports",
        ["expires_at", "id"],
        schema="mangasensei",
    )

    op.create_table(
        "document_import_capabilities",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_import_id", sa.BigInteger(), nullable=False),
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
            "scope = 'read:document-import'",
            name=op.f("ck_document_import_capabilities_scope"),
        ),
        sa.CheckConstraint(
            "octet_length(digest) = 32",
            name=op.f("ck_document_import_capabilities_digest_length"),
        ),
        sa.ForeignKeyConstraint(
            ["document_import_id"],
            ["mangasensei.document_imports.id"],
            name=op.f("fk_document_import_capabilities_document_import_id_document_imports"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_import_capabilities")),
        sa.UniqueConstraint(
            "key_id",
            "digest",
            name=op.f("uq_document_import_capabilities_key_id_digest"),
        ),
        schema="mangasensei",
    )
    op.create_index(
        "ix_document_import_capabilities_import_id",
        "document_import_capabilities",
        ["document_import_id", "id"],
        schema="mangasensei",
    )


def downgrade() -> None:
    bind = op.get_bind()
    incompatible = bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM mangasensei.document_imports) "
            "OR EXISTS (SELECT 1 FROM mangasensei.documents WHERE source_kind = 'pdf')"
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError("cannot safely downgrade while PDF import state exists")

    op.drop_index(
        "ix_document_import_capabilities_import_id",
        table_name="document_import_capabilities",
        schema="mangasensei",
    )
    op.drop_table("document_import_capabilities", schema="mangasensei")
    op.drop_index(
        "ix_document_imports_expires_at_id",
        table_name="document_imports",
        schema="mangasensei",
    )
    op.drop_index(
        "ix_document_imports_source_expiry",
        table_name="document_imports",
        schema="mangasensei",
    )
    op.drop_index(
        "ix_document_imports_status_lease",
        table_name="document_imports",
        schema="mangasensei",
    )
    op.drop_table("document_imports", schema="mangasensei")

    op.drop_constraint(
        op.f("ck_documents_source_kind"),
        "documents",
        schema="mangasensei",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_documents_source_kind"),
        "documents",
        "source_kind IN ('images')",
        schema="mangasensei",
    )
