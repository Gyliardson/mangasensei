from __future__ import annotations

import hashlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

_PREVIOUS_REVISION = "4b913c2a7e56"
_NEW_REVISION = "9c2e7d4a1160"


def _alembic_config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.integration
def test_dictionary_projection_migration_backfill_and_lossy_downgrade(
    clean_postgres_url: str,
) -> None:
    config = _alembic_config(clean_postgres_url)
    command.downgrade(config, _PREVIOUS_REVISION)
    engine = create_engine(clean_postgres_url)

    image_digest = hashlib.sha256(b"dictionary-migration-image").digest()
    request_digest = hashlib.sha256(b"dictionary-migration-request").digest()
    dictionary_digest = hashlib.sha256(b"dictionary-migration-jmdict").digest()
    dictionary_version = "JMdict migration fixture 20260810"

    with engine.begin() as connection:
        blob_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.image_blobs
                    (sha256, byte_size, width, height, media_type, storage_key)
                VALUES
                    (:sha256, 100, 80, 120, 'image/png', :storage_key)
                RETURNING id
                """
            ),
            {"sha256": image_digest, "storage_key": f"objects/{image_digest.hex()}"},
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
                    (:blob_id, 'legacy-dictionary.png', 'v1', :upload_digest, :request_digest)
                RETURNING id
                """
            ),
            {
                "blob_id": blob_id,
                "upload_digest": hashlib.sha256(b"dictionary-migration-upload").digest(),
                "request_digest": request_digest,
            },
        ).scalar_one()
        job_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.jobs
                    (
                        page_id, job_kind, study_language, idempotency_digest,
                        request_digest, status, attempt_count, fencing_token,
                        started_at, finished_at
                    )
                VALUES
                    (
                        :page_id, 'page_analysis', 'en', :idempotency_digest,
                        :request_digest, 'completed', 1, 1, now(), now()
                    )
                RETURNING id
                """
            ),
            {
                "page_id": page_id,
                "idempotency_digest": hashlib.sha256(b"dictionary-migration-job").digest(),
                "request_digest": request_digest,
            },
        ).scalar_one()
        ocr_run_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.ocr_runs
                    (
                        job_id, fencing_token, detector, recognizer,
                        model_manifest_version, config_digest,
                        upstream_repository, upstream_commit, input_sha256,
                        width, height
                    )
                VALUES
                    (
                        :job_id, 1, 'fixture-detector', 'fixture-recognizer',
                        'fixture-manifest', :config_digest,
                        'https://example.invalid/ocr', 'fixture-commit', :input_sha256,
                        80, 120
                    )
                RETURNING id
                """
            ),
            {
                "job_id": job_id,
                "config_digest": hashlib.sha256(b"dictionary-migration-ocr-config").digest(),
                "input_sha256": image_digest,
            },
        ).scalar_one()
        region_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.ocr_regions
                    (
                        ocr_run_id, region_ordinal, reading_order,
                        x, y, width, height,
                        normalized_x, normalized_y, normalized_width, normalized_height,
                        angle, confidence, raw_text
                    )
                VALUES
                    (
                        :ocr_run_id, 0, 0,
                        10, 20, 40, 60,
                        0.125, 0.1666666667, 0.5, 0.5,
                        0, 0.98, '猫'
                    )
                RETURNING id
                """
            ),
            {"ocr_run_id": ocr_run_id},
        ).scalar_one()
        linguistic_run_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.linguistic_runs
                    (
                        job_id, ocr_run_id, fencing_token,
                        tokenizer_name, tokenizer_version, config_digest,
                        dictionary_name, dictionary_version, dictionary_digest,
                        input_digest
                    )
                VALUES
                    (
                        :job_id, :ocr_run_id, 1,
                        'SudachiPy', 'fixture', :config_digest,
                        'JMdict', :dictionary_version, :dictionary_digest,
                        :input_digest
                    )
                RETURNING id
                """
            ),
            {
                "job_id": job_id,
                "ocr_run_id": ocr_run_id,
                "config_digest": hashlib.sha256(b"dictionary-migration-linguistic-config").digest(),
                "dictionary_version": dictionary_version,
                "dictionary_digest": dictionary_digest,
                "input_digest": hashlib.sha256(b"dictionary-migration-input").digest(),
            },
        ).scalar_one()
        lexical_match_id = connection.execute(
            text(
                """
                INSERT INTO mangasensei.lexical_matches
                    (
                        linguistic_run_id, region_id, stable_key,
                        start_token_ordinal, end_token_ordinal,
                        surface, display_lemma, display_reading,
                        dictionary_namespace, dictionary_entry_id,
                        form_lemma, form_reading, dictionary_source,
                        jlpt_level, jlpt_official
                    )
                VALUES
                    (
                        :linguistic_run_id, :region_id, :stable_key,
                        0, 1,
                        '猫', '猫', 'ねこ',
                        'JMdict', 'jmdict-cat',
                        '猫', 'ねこ', :dictionary_source,
                        NULL, false
                    )
                RETURNING id
                """
            ),
            {
                "linguistic_run_id": linguistic_run_id,
                "region_id": region_id,
                "stable_key": hashlib.sha256(b"dictionary-migration-lexical").digest(),
                "dictionary_source": dictionary_version,
            },
        ).scalar_one()
        connection.execute(
            text(
                """
                INSERT INTO mangasensei.lexical_meanings
                    (lexical_match_id, meaning_ordinal, meaning)
                VALUES (:lexical_match_id, 0, 'cat'), (:lexical_match_id, 1, 'domestic cat')
                """
            ),
            {"lexical_match_id": lexical_match_id},
        )
        connection.execute(
            text(
                """
                INSERT INTO mangasensei.study_results
                    (
                        job_id, linguistic_run_id, content_language,
                        study_language, dictionary_language
                    )
                VALUES (:job_id, :linguistic_run_id, 'ja', 'en', 'en')
                """
            ),
            {"job_id": job_id, "linguistic_run_id": linguistic_run_id},
        )

    command.upgrade(config, _NEW_REVISION)

    expected_ref = f"jmdict:en:{dictionary_version}:{dictionary_digest.hex()[:16]}"
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        projection = connection.execute(
            text(
                """
                SELECT linguistic_run_id, requested_dictionary_language,
                       fallback_dictionary_language
                FROM mangasensei.dictionary_projections
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        ).one()
        source = connection.execute(
            text(
                """
                SELECT source_ref, dataset, product_language, source_version,
                       normalized_digest
                FROM mangasensei.dictionary_projection_sources
                WHERE projection_job_id = :job_id
                """
            ),
            {"job_id": job_id},
        ).one()
        item = connection.execute(
            text(
                """
                SELECT effective_dictionary_language, fallback_used,
                       fallback_reason, source_ref
                FROM mangasensei.dictionary_projection_items
                WHERE projection_job_id = :job_id
                  AND lexical_match_id = :lexical_match_id
                """
            ),
            {"job_id": job_id, "lexical_match_id": lexical_match_id},
        ).one()
        meanings = connection.execute(
            text(
                """
                SELECT meaning
                FROM mangasensei.dictionary_projection_meanings
                WHERE projection_job_id = :job_id
                  AND lexical_match_id = :lexical_match_id
                ORDER BY meaning_ordinal
                """
            ),
            {"job_id": job_id, "lexical_match_id": lexical_match_id},
        ).scalars().all()

    assert revision == _NEW_REVISION
    assert projection == (linguistic_run_id, "en", "en")
    assert source == (expected_ref, "JMdict", "en", dictionary_version, dictionary_digest)
    assert item == ("en", False, None, expected_ref)
    assert meanings == ["cat", "domestic cat"]

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE mangasensei.dictionary_projections
                SET requested_dictionary_language = 'de'
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        )
    with pytest.raises(DBAPIError, match="cannot downgrade while multilingual"):
        command.downgrade(config, _PREVIOUS_REVISION)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE mangasensei.dictionary_projections
                SET requested_dictionary_language = 'en'
                WHERE job_id = :job_id
                """
            ),
            {"job_id": job_id},
        )

    # Pure English state can downgrade because the legacy schema represents it losslessly.
    command.downgrade(config, _PREVIOUS_REVISION)
    command.upgrade(config, "head")
    engine.dispose()