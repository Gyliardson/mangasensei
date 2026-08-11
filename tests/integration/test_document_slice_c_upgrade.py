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

_SLICE_B = "e2f6a0c84b11"
_SLICE_C = "f6a3c2d91b47"


def _config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@contextmanager
def _isolated_database(postgres_url: str) -> Iterator[str]:
    source_url = make_url(postgres_url)
    database_name = f"mangasensei_slice_c_{uuid4().hex}"
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
def test_slice_c_upgrade_preserves_document_jobs_standalone_pages_and_retention(
    postgres_url: str,
) -> None:
    with _isolated_database(postgres_url) as isolated_url:
        config = _config(isolated_url)
        command.upgrade(config, _SLICE_B)
        engine = create_engine(isolated_url)
        digest = hashlib.sha256(b"slice-c-upgrade-image").digest()
        standalone_upload = hashlib.sha256(b"slice-c-standalone-upload").digest()
        child_job_digest = hashlib.sha256(b"slice-c-child-job").digest()
        try:
            with engine.begin() as connection:
                document_id = connection.execute(
                    text("INSERT INTO mangasensei.documents DEFAULT VALUES RETURNING id")
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
                    {"digest": digest, "storage_key": f"objects/{digest.hex()}"},
                ).scalar_one()
                child_id = connection.execute(
                    text(
                        """
                        INSERT INTO mangasensei.pages
                            (image_blob_id, document_id, ordinal, original_filename,
                             upload_key_id, upload_idempotency_digest, request_digest, expires_at)
                        VALUES (:blob_id, :document_id, 0, 'child.png', NULL, NULL,
                                :digest, :expires_at)
                        RETURNING id
                        """
                    ),
                    {
                        "blob_id": blob_id,
                        "document_id": document_id,
                        "digest": digest,
                        "expires_at": document_expiry,
                    },
                ).scalar_one()
                standalone_id = connection.execute(
                    text(
                        """
                        INSERT INTO mangasensei.pages
                            (image_blob_id, original_filename, upload_key_id,
                             upload_idempotency_digest, request_digest)
                        VALUES (:blob_id, 'standalone.png', 'v1', :upload_digest, :digest)
                        RETURNING id
                        """
                    ),
                    {
                        "blob_id": blob_id,
                        "upload_digest": standalone_upload,
                        "digest": digest,
                    },
                ).scalar_one()
                job_id = connection.execute(
                    text(
                        """
                        INSERT INTO mangasensei.jobs
                            (page_id, idempotency_digest, request_digest)
                        VALUES (:page_id, :job_digest, :digest)
                        RETURNING id
                        """
                    ),
                    {"page_id": child_id, "job_digest": child_job_digest, "digest": digest},
                ).scalar_one()

            command.upgrade(config, _SLICE_C)

            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                child = connection.execute(
                    text(
                        "SELECT document_id, ordinal, expires_at "
                        "FROM mangasensei.pages WHERE id=:id"
                    ),
                    {"id": child_id},
                ).one()
                standalone = connection.execute(
                    text(
                        "SELECT document_id, upload_idempotency_digest "
                        "FROM mangasensei.pages WHERE id=:id"
                    ),
                    {"id": standalone_id},
                ).one()
                job = connection.execute(
                    text(
                        "SELECT status, cancel_requested_at, document_retry_request_id "
                        "FROM mangasensei.jobs WHERE id=:id"
                    ),
                    {"id": job_id},
                ).one()
                retry_table_exists = connection.execute(
                    text("SELECT to_regclass('mangasensei.document_retry_requests') IS NOT NULL")
                ).scalar_one()

            assert revision == _SLICE_C
            assert child == (document_id, 0, document_expiry)
            assert standalone.document_id is None
            assert bytes(standalone.upload_idempotency_digest) == standalone_upload
            assert job == ("pending", None, None)
            assert retry_table_exists is True

            with engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        UPDATE mangasensei.jobs
                        SET status='cancelled', cancel_requested_at=now(), finished_at=now()
                        WHERE id=:id
                        """
                    ),
                    {"id": job_id},
                )
                cancelled = connection.execute(
                    text("SELECT status, error_detail FROM mangasensei.jobs WHERE id=:id"),
                    {"id": job_id},
                ).one()
                connection.execute(
                    text("UPDATE mangasensei.jobs SET status='expired' WHERE id=:id"),
                    {"id": job_id},
                )
                expired = connection.execute(
                    text(
                        "SELECT status, cancel_requested_at IS NOT NULL "
                        "FROM mangasensei.jobs WHERE id=:id"
                    ),
                    {"id": job_id},
                ).one()
            assert cancelled == ("cancelled", None)
            assert expired == ("expired", True)

            with pytest.raises(RuntimeError, match="cannot safely downgrade Slice C"):
                command.downgrade(config, _SLICE_B)
        finally:
            engine.dispose()


@pytest.mark.integration
def test_slice_c_trigger_preserves_dictionary_claimed_direct_completion(
    postgres_url: str,
) -> None:
    with _isolated_database(postgres_url) as isolated_url:
        config = _config(isolated_url)
        command.upgrade(config, _SLICE_C)
        engine = create_engine(isolated_url)
        digest = hashlib.sha256(b"slice-c-dictionary-transition").digest()
        try:
            with engine.begin() as connection:
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
                page_id = connection.execute(
                    text(
                        """
                        INSERT INTO mangasensei.pages
                            (image_blob_id, original_filename, upload_key_id,
                             upload_idempotency_digest, request_digest)
                        VALUES (:blob_id, 'dictionary.png', 'v1', :upload_digest, :digest)
                        RETURNING id
                        """
                    ),
                    {
                        "blob_id": blob_id,
                        "upload_digest": hashlib.sha256(b"slice-c-dictionary-upload").digest(),
                        "digest": digest,
                    },
                ).scalar_one()
                job_id = connection.execute(
                    text(
                        """
                        INSERT INTO mangasensei.jobs
                            (page_id, job_kind, idempotency_digest, request_digest)
                        VALUES (
                            :page_id, 'dictionary_language_reprocess', :job_digest, :digest
                        )
                        RETURNING id
                        """
                    ),
                    {
                        "page_id": page_id,
                        "job_digest": hashlib.sha256(b"slice-c-dictionary-job").digest(),
                        "digest": digest,
                    },
                ).scalar_one()
                connection.execute(
                    text(
                        """
                        UPDATE mangasensei.jobs
                        SET status='claimed', worker_id='slice-c-migration-test',
                            attempt_count=1, fencing_token=1, claimed_at=now(),
                            heartbeat_at=now(), lease_expires_at=now() + interval '60 seconds'
                        WHERE id=:id
                        """
                    ),
                    {"id": job_id},
                )
                connection.execute(
                    text(
                        """
                        UPDATE mangasensei.jobs
                        SET status='completed', worker_id=NULL, heartbeat_at=NULL,
                            lease_expires_at=NULL, finished_at=now()
                        WHERE id=:id
                        """
                    ),
                    {"id": job_id},
                )
                status = connection.execute(
                    text("SELECT status FROM mangasensei.jobs WHERE id=:id"),
                    {"id": job_id},
                ).scalar_one()
            assert status == "completed"
        finally:
            engine.dispose()


@pytest.mark.integration
def test_slice_c_clean_upgrade_can_losslessly_downgrade_to_slice_b(postgres_url: str) -> None:
    with _isolated_database(postgres_url) as isolated_url:
        config = _config(isolated_url)
        command.upgrade(config, _SLICE_C)
        command.downgrade(config, _SLICE_B)
        engine = create_engine(isolated_url)
        try:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                retry_table_exists = connection.execute(
                    text("SELECT to_regclass('mangasensei.document_retry_requests') IS NOT NULL")
                ).scalar_one()
            assert revision == _SLICE_B
            assert retry_table_exists is False
        finally:
            engine.dispose()