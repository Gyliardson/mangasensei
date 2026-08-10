from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from mangasensei.domain.capabilities import DocumentCapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService

_PRE_SLICE_B = "9c2e7d4a1160"
_SLICE_B = "e2f6a0c84b11"
_CAPABILITY_PEPPER = "slice-b-migration-capability-pepper-00000001"


def _config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@contextmanager
def _isolated_database(postgres_url: str) -> Iterator[str]:
    source_url = make_url(postgres_url)
    database_name = f"mangasensei_slice_b_{uuid4().hex}"
    admin_engine = create_engine(
        source_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
    )
    target_url = source_url.set(database=database_name).render_as_string(hide_password=False)

    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        yield target_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        admin_engine.dispose()


@pytest.mark.integration
def test_slice_b_upgrade_preserves_slice_a_document_and_standalone_identity(
    postgres_url: str,
) -> None:
    with _isolated_database(postgres_url) as isolated_url:
        config = _config(isolated_url)
        command.upgrade(config, _PRE_SLICE_B)
        engine = create_engine(isolated_url)
        image_digest = hashlib.sha256(b"slice-a-document-image").digest()
        child_upload_digest = hashlib.sha256(b"slice-a-child-upload").digest()
        standalone_upload_digest = hashlib.sha256(b"slice-a-standalone-upload").digest()
        capability_service = CapabilityService((_CAPABILITY_PEPPER,))

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
                read_capability = capability_service.issue(
                    resource_id=str(document_public_id),
                    scope=DocumentCapabilityScope.READ_DOCUMENT,
                    expires_at=document_expiry,
                )
                read_digest = bytes.fromhex(read_capability.persisted_digest)
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

            command.upgrade(config, _SLICE_B)

            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
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
                persisted_capability = connection.execute(
                    text(
                        """
                        SELECT scope, digest, expires_at
                        FROM mangasensei.document_capabilities
                        WHERE document_id = :id
                        """
                    ),
                    {"id": document_id},
                ).one()

            assert revision == _SLICE_B
            assert child_identity == (None, None, document_id, 0)
            assert standalone_identity == ("v1", standalone_upload_digest, None)
            assert document == (document_public_id, None, None, None)
            assert persisted_capability.scope == "read:document"
            assert capability_service.verify(
                token=read_capability.token,
                persisted_digest=bytes(persisted_capability.digest).hex(),
                resource_id=str(document_public_id),
                scope=DocumentCapabilityScope.READ_DOCUMENT,
                expires_at=persisted_capability.expires_at,
            )
        finally:
            engine.dispose()
