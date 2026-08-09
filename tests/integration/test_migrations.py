from __future__ import annotations

import hashlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def alembic_config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.integration
def test_migrations_upgrade_downgrade_and_reupgrade(postgres_url: str) -> None:
    config = alembic_config(postgres_url)

    command.upgrade(config, "head")
    engine = create_engine(postgres_url)
    assert {
        "image_blobs",
        "pages",
        "page_capabilities",
        "jobs",
        "job_attempts",
        "ocr_runs",
        "ocr_regions",
        "linguistic_runs",
        "linguistic_tokens",
        "gemini_calls",
        "gemini_analyses",
        "gemini_cost_ledger",
        "rate_limit_buckets",
        "study_results",
    }.issubset(inspect(engine).get_table_names(schema="mangasensei"))

    command.downgrade(config, "base")
    assert "pages" not in inspect(engine).get_table_names(schema="mangasensei")

    command.upgrade(config, "head")
    with engine.connect() as connection:
        heads = connection.execute(text("SELECT count(*) FROM alembic_version")).scalar_one()
    assert heads == 1
    engine.dispose()


@pytest.mark.integration
def test_language_migration_backfills_existing_completed_analysis_as_pt_br(
    postgres_url: str,
) -> None:
    config = alembic_config(postgres_url)
    command.upgrade(config, "a17e52c4d908")
    engine = create_engine(postgres_url)
    digest = hashlib.sha256(b"pre-language-analysis").digest()
    config_digest = hashlib.sha256(b"pre-language-config").digest()
    dictionary_digest = hashlib.sha256(b"pre-language-dictionary").digest()
    input_digest = hashlib.sha256(b"pre-language-input").digest()

    with engine.begin() as connection:
        blob_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.image_blobs
                    (sha256, byte_size, width, height, media_type, storage_key)
                VALUES
                    (:digest, 100, 10, 10, 'image/png', :storage_key)
                RETURNING id
                """
            ),
            {"digest": digest, "storage_key": f"objects/{digest.hex()}"},
        ).scalar_one()
        page_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.pages
                    (
                        image_blob_id,
                        original_filename,
                        upload_key_id,
                        upload_idempotency_digest,
                        request_digest
                    )
                VALUES
                    (:blob_id, 'legacy.png', 'v1', :upload_digest, :request_digest)
                RETURNING id
                """
            ),
            {
                "blob_id": blob_id,
                "upload_digest": hashlib.sha256(b"legacy-upload").digest(),
                "request_digest": digest,
            },
        ).scalar_one()
        job_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.jobs
                    (
                        page_id,
                        idempotency_digest,
                        request_digest,
                        status,
                        attempt_count,
                        fencing_token,
                        started_at,
                        finished_at
                    )
                VALUES
                    (
                        :page_id,
                        :job_digest,
                        :request_digest,
                        'completed',
                        1,
                        1,
                        now(),
                        now()
                    )
                RETURNING id
                """
            ),
            {
                "page_id": page_id,
                "job_digest": hashlib.sha256(b"legacy-job").digest(),
                "request_digest": digest,
            },
        ).scalar_one()
        ocr_run_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.ocr_runs
                    (
                        job_id,
                        fencing_token,
                        detector,
                        recognizer,
                        model_manifest_version,
                        config_digest,
                        upstream_repository,
                        upstream_commit,
                        input_sha256,
                        width,
                        height
                    )
                VALUES
                    (
                        :job_id,
                        1,
                        'legacy-detector',
                        'legacy-recognizer',
                        'legacy-manifest',
                        :config_digest,
                        'https://example.invalid/legacy',
                        'legacy-commit',
                        :input_sha256,
                        10,
                        10
                    )
                RETURNING id
                """
            ),
            {
                "job_id": job_id,
                "config_digest": config_digest,
                "input_sha256": digest,
            },
        ).scalar_one()
        linguistic_run_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.linguistic_runs
                    (
                        job_id,
                        ocr_run_id,
                        fencing_token,
                        tokenizer_name,
                        tokenizer_version,
                        config_digest,
                        dictionary_name,
                        dictionary_version,
                        dictionary_digest,
                        input_digest
                    )
                VALUES
                    (
                        :job_id,
                        :ocr_run_id,
                        1,
                        'SudachiPy',
                        'legacy',
                        :config_digest,
                        'JMdict',
                        'legacy',
                        :dictionary_digest,
                        :input_digest
                    )
                RETURNING id
                """
            ),
            {
                "job_id": job_id,
                "ocr_run_id": ocr_run_id,
                "config_digest": config_digest,
                "dictionary_digest": dictionary_digest,
                "input_digest": input_digest,
            },
        ).scalar_one()

    command.upgrade(config, "head")

    with engine.connect() as connection:
        job_language = connection.execute(
            text("SELECT study_language FROM mangasensei.jobs WHERE id = :job_id"),
            {"job_id": job_id},
        ).scalar_one()
        result = connection.execute(
            text(
                """
                SELECT
                    linguistic_run_id,
                    content_language,
                    study_language,
                    dictionary_language
                FROM mangasensei.study_results
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        ).one()

    assert job_language == "pt-BR"
    assert result == (linguistic_run_id, "ja", "pt-BR", "en")
    engine.dispose()
