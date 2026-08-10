from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


@pytest.mark.integration
def test_document_schema_preserves_standalone_page_idempotency_contract(
    clean_postgres_url: str,
) -> None:
    engine = create_engine(clean_postgres_url)
    inspector = inspect(engine)

    assert {"documents", "document_capabilities"}.issubset(
        inspector.get_table_names(schema="mangasensei")
    )
    page_columns = {
        column["name"]: column for column in inspector.get_columns("pages", schema="mangasensei")
    }
    assert page_columns["document_id"]["nullable"] is True
    assert page_columns["ordinal"]["nullable"] is True
    assert page_columns["upload_key_id"]["nullable"] is False
    assert page_columns["upload_idempotency_digest"]["nullable"] is False
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert revision == "b7d2f4a91c63"
    engine.dispose()


@pytest.mark.integration
def test_document_page_membership_constraints_reject_invalid_ordering(
    clean_postgres_url: str,
) -> None:
    engine = create_engine(clean_postgres_url)
    digest = hashlib.sha256(b"membership-constraints").digest()
    with engine.begin() as connection:
        document_id = connection.execute(
            text("INSERT INTO mangasensei.documents DEFAULT VALUES RETURNING id")
        ).scalar_one()
        blob_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.image_blobs
                    (sha256, byte_size, width, height, media_type, storage_key)
                VALUES (:digest, 100, 10, 10, 'image/png', :storage_key)
                RETURNING id
                """
            ),
            {"digest": digest, "storage_key": f"objects/{digest.hex()}"},
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO mangasensei.pages
                    (
                        image_blob_id, document_id, ordinal, original_filename,
                        upload_key_id, upload_idempotency_digest, request_digest
                    )
                VALUES
                    (:blob_id, :document_id, 0, 'valid.png', 'v1', :upload_digest, :digest)
                """
            ),
            {
                "blob_id": blob_id,
                "document_id": document_id,
                "upload_digest": hashlib.sha256(b"valid").digest(),
                "digest": digest,
            },
        )

    invalid_statements = (
        (
            """
            INSERT INTO mangasensei.pages
                (
                    image_blob_id, document_id, original_filename,
                    upload_key_id, upload_idempotency_digest, request_digest
                )
            VALUES (:blob_id, :document_id, 'missing-ordinal.png', 'v1', :upload_digest, :digest)
            """,
            b"missing-ordinal",
        ),
        (
            """
            INSERT INTO mangasensei.pages
                (
                    image_blob_id, document_id, ordinal, original_filename,
                    upload_key_id, upload_idempotency_digest, request_digest
                )
            VALUES (:blob_id, :document_id, -1, 'negative.png', 'v1', :upload_digest, :digest)
            """,
            b"negative",
        ),
        (
            """
            INSERT INTO mangasensei.pages
                (
                    image_blob_id, document_id, ordinal, original_filename,
                    upload_key_id, upload_idempotency_digest, request_digest
                )
            VALUES (:blob_id, :document_id, 0, 'duplicate.png', 'v1', :upload_digest, :digest)
            """,
            b"duplicate",
        ),
    )
    for statement, key in invalid_statements:
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text(statement),
                {
                    "blob_id": blob_id,
                    "document_id": document_id,
                    "upload_digest": hashlib.sha256(key).digest(),
                    "digest": digest,
                },
            )

    engine.dispose()
