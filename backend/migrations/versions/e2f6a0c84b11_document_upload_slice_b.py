"""add document creation identity and reprocess capability

Revision ID: e2f6a0c84b11
Revises: 9c2e7d4a1160
Create Date: 2026-08-10 16:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f6a0c84b11"
down_revision: str | None = "9c2e7d4a1160"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("upload_key_id", sa.String(length=32), nullable=True),
        schema="mangasensei",
    )
    op.add_column(
        "documents",
        sa.Column("upload_idempotency_digest", sa.LargeBinary(length=32), nullable=True),
        schema="mangasensei",
    )
    op.add_column(
        "documents",
        sa.Column("request_digest", sa.LargeBinary(length=32), nullable=True),
        schema="mangasensei",
    )
    op.create_unique_constraint(
        op.f("uq_documents_upload_key_id_upload_idempotency_digest"),
        "documents",
        ["upload_key_id", "upload_idempotency_digest"],
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_documents_creation_identity_triplet"),
        "documents",
        "(upload_key_id IS NULL AND upload_idempotency_digest IS NULL AND request_digest IS NULL) OR "
        "(upload_key_id IS NOT NULL AND upload_idempotency_digest IS NOT NULL AND request_digest IS NOT NULL)",
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_documents_idempotency_digest_length"),
        "documents",
        "upload_idempotency_digest IS NULL OR octet_length(upload_idempotency_digest) = 32",
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_documents_request_digest_length"),
        "documents",
        "request_digest IS NULL OR octet_length(request_digest) = 32",
        schema="mangasensei",
    )

    # Slice A could only seed document children by filling the then-NOT-NULL standalone
    # upload identity. That identity was never a public document-child contract, so clear
    # it before enforcing the Slice-B ownership invariant. Standalone Page rows are untouched.
    op.execute(
        sa.text(
            "UPDATE mangasensei.pages "
            "SET upload_key_id = NULL, upload_idempotency_digest = NULL "
            "WHERE document_id IS NOT NULL"
        )
    )
    op.alter_column(
        "pages",
        "upload_key_id",
        existing_type=sa.String(length=32),
        nullable=True,
        schema="mangasensei",
    )
    op.alter_column(
        "pages",
        "upload_idempotency_digest",
        existing_type=sa.LargeBinary(length=32),
        nullable=True,
        schema="mangasensei",
    )
    op.create_check_constraint(
        op.f("ck_pages_standalone_idempotency_pair"),
        "pages",
        "(document_id IS NULL AND upload_key_id IS NOT NULL AND upload_idempotency_digest IS NOT NULL) OR "
        "(document_id IS NOT NULL AND upload_key_id IS NULL AND upload_idempotency_digest IS NULL)",
        schema="mangasensei",
    )

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


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_document_capabilities_scope"),
        "document_capabilities",
        schema="mangasensei",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_document_capabilities_scope"),
        "document_capabilities",
        "scope IN ('read:document','read:document-image')",
        schema="mangasensei",
    )

    op.drop_constraint(
        op.f("ck_pages_standalone_idempotency_pair"),
        "pages",
        schema="mangasensei",
        type_="check",
    )
    # The pre-Slice-B schema requires non-null upload identity on every Page. Reconstruct
    # a deterministic, downgrade-only placeholder for document children from public
    # Document UUID + ordinal. This does not alter standalone upload identities.
    op.execute(
        sa.text(
            "UPDATE mangasensei.pages AS p SET "
            "upload_key_id = 'document-v1-downgrade', "
            "upload_idempotency_digest = decode(" 
            "replace(d.public_id::text, '-', '') || lpad(to_hex(p.ordinal::bigint), 32, '0'), 'hex') "
            "FROM mangasensei.documents AS d "
            "WHERE p.document_id = d.id AND p.upload_key_id IS NULL"
        )
    )
    op.alter_column(
        "pages",
        "upload_idempotency_digest",
        existing_type=sa.LargeBinary(length=32),
        nullable=False,
        schema="mangasensei",
    )
    op.alter_column(
        "pages",
        "upload_key_id",
        existing_type=sa.String(length=32),
        nullable=False,
        schema="mangasensei",
    )

    op.drop_constraint(
        op.f("ck_documents_request_digest_length"),
        "documents",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_documents_idempotency_digest_length"),
        "documents",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_documents_creation_identity_triplet"),
        "documents",
        schema="mangasensei",
        type_="check",
    )
    op.drop_constraint(
        op.f("uq_documents_upload_key_id_upload_idempotency_digest"),
        "documents",
        schema="mangasensei",
        type_="unique",
    )
    op.drop_column("documents", "request_digest", schema="mangasensei")
    op.drop_column("documents", "upload_idempotency_digest", schema="mangasensei")
    op.drop_column("documents", "upload_key_id", schema="mangasensei")
