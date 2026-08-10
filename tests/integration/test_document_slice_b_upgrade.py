from __future__ import annotations

import hashlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

_PRE_SLICE_B = "9c2e7d4a1160"
_SLICE_B = "e2f6a0c84b11"


def _config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.integration
def test_slice_b_upgrade_preserves_slice_a_document_and_standalone_identity(
    postgres_url: str,
) -> None:
    config = _config(postgres_url)
    command.upgrade(config, _PRE_SLICE_B)
    engine = create_engine(postgres_url)
    image_digest = hashlib.sha256(b"slice-a-document-image").digest()
    child_upload_digest = hashlib.sha256(b"slice-a-child-upload").digest()
    standalone_upload_digest = hashlib.sha256(b"slice-a-standalone-upload").digest()
    read_digest = hashlib.sha256(b"slice-a-read-capability").digest()

    try:
        with engine.begin() as connection:
            document_id = connection.execute(
                text("INSERT INTO mangasensei.documents DEFAULT VALUES RETURNING id")
            ).scalar_one()
            document_public_id = connection.execute(
                text("SELECT public_id FROM mangasensei.documents WHERE id = :id"),
                {"id": document_id},
            ).scalar_one()
            document_expiry = connection.execute(
                text("SELECT expires_at FROM mangasensei.documents WHERE id = :id"),
                {"id": document_id},
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
                {"digest": image_digest, "storage_key": f"objects/{image_digest.hex()}"},
            ).scalar_one()
            child_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.pages
                        (
                            image_blob_id, document_id, ordinal, original_filename,
                            upload_key_id, upload_idempotency_digest, request_digest,
                            expires_at
                        )
                    VALUES
                        (
                            :blob_id, :document_id, 0, 'child.png',
                            'v1', :upload_digest, :request_digest, :expires_at
                        )
                    RETURNING id
                    """
                ),
                {
                    "blob_id": blob_id,
                    "document_id": document_id,
                    "upload_digest": child_upload_digest,
                    "request_digest": image_digest,
                    "expires_at": document_expiry,
                },
            ).scalar_one()
            standalone_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.pages
                        (
                            image_blob_id, original_filename, upload_key_id,
                            upload_idempotency_digest, request_digest
                        )
                    VALUES
                        (:blob_id, 'standalone.png', 'v1', :upload_digest, :request_digest)
                    RETURNING id
                    """
                ),
                {
                    "blob_id": blob_id,
                    "upload_digest": standalone_upload_digest,
                    "request_digest": image_digest,
                },
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.document_capabilities
                        (document_id, key_id, scope, digest, expires_at)
                    VALUES (:document_id, 'v1', 'read:document', :digest, :expires_at)
                    """
                ),
                {
                    "document_id": document_id,
                    "digest": read_digest,
                    "expires_at": document_expiry,
                },
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            child_identity = connection.execute(
                text(
                    """
                    SELECT upload_key_id, upload_idempotency_digest, document_id, ordinal
                    FROM mangasensei.pages WHERE id = :id
                    """
                ),
                {"id": child_id},
            ).one()
            standalone_identity = connection.execute(
                text(
                    """
                    SELECT upload_key_id, upload_idempotency_digest, document_id
                    FROM mangasensei.pages WHERE id = :id
                    """
                ),
                {"id": standalone_id},
            ).one()
            document = connection.execute(
                text(
                    """
                    SELECT public_id, upload_key_id, upload_idempotency_digest, request_digest
                    FROM mangasensei.documents WHERE id = :id
                    """
                ),
                {"id": document_id},
            ).one()
            read_scope = connection.execute(
                text(
                    "SELECT scope FROM mangasensei.document_capabilities WHERE document_id = :id"
                ),
                {"id": document_id},
            ).scalar_one()

        assert revision == _SLICE_B
        assert child_identity == (None, None, document_id, 0)
        assert standalone_identity == ("v1", standalone_upload_digest, None)
        assert document == (document_public_id, None, None, None)
        assert read_scope == "read:document"
    finally:
        engine.dispose()
        command.upgrade(config, "head")
