from __future__ import annotations

import hashlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

_PREVIOUS_HEAD = "b7d2f4a91c63"
_CURRENT_HEAD = "e2f6a0c84b11"


def _config(database_url: str) -> Config:
    config = Config("backend/alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.integration
def test_lexical_match_migration_backfills_resolved_token_meanings_and_gemini_link(
    postgres_url: str,
) -> None:
    config = _config(postgres_url)
    command.upgrade(config, "head")
    command.downgrade(config, _PREVIOUS_HEAD)
    engine = create_engine(postgres_url)
    digest = hashlib.sha256(b"lexical-backfill").digest()
    config_digest = hashlib.sha256(b"lexical-config").digest()
    dictionary_digest = hashlib.sha256(b"lexical-dictionary").digest()
    input_digest = hashlib.sha256(b"lexical-input").digest()
    stable_key = hashlib.sha256(b"legacy-token").digest()

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
                        (
                            image_blob_id, original_filename, upload_key_id,
                            upload_idempotency_digest, request_digest
                        )
                    VALUES (:blob_id, 'legacy.png', 'v1', :upload_digest, :digest)
                    RETURNING id
                    """
                ),
                {
                    "blob_id": blob_id,
                    "upload_digest": hashlib.sha256(b"lexical-upload").digest(),
                    "digest": digest,
                },
            ).scalar_one()
            job_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.jobs
                        (
                            page_id, idempotency_digest, request_digest, status,
                            attempt_count, fencing_token, started_at, finished_at
                        )
                    VALUES
                        (:page_id, :job_digest, :digest, 'completed', 1, 1, now(), now())
                    RETURNING id
                    """
                ),
                {
                    "page_id": page_id,
                    "job_digest": hashlib.sha256(b"lexical-job").digest(),
                    "digest": digest,
                },
            ).scalar_one()
            ocr_run_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.ocr_runs
                        (
                            job_id, fencing_token, detector, recognizer,
                            model_manifest_version, config_digest, upstream_repository,
                            upstream_commit, input_sha256, width, height
                        )
                    VALUES
                        (
                            :job_id, 1, 'legacy-detector', 'legacy-recognizer',
                            'legacy-manifest', :config_digest, 'https://example.invalid/legacy',
                            'legacy-commit', :digest, 10, 10
                        )
                    RETURNING id
                    """
                ),
                {"job_id": job_id, "config_digest": config_digest, "digest": digest},
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
                            0, 0, 10, 10,
                            0, 0, 1, 1,
                            0, 1, 'ノマ'
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
                            dictionary_name, dictionary_version, dictionary_digest, input_digest
                        )
                    VALUES
                        (
                            :job_id, :ocr_run_id, 1,
                            'SudachiPy', '0.6.11', :config_digest,
                            'JMdict', 'legacy-v3', :dictionary_digest, :input_digest
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
            token_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.linguistic_tokens
                        (
                            linguistic_run_id, region_id, token_ordinal, stable_key,
                            start_offset, end_offset, surface, lemma, reading, part_of_speech,
                            dictionary_entry_id, dictionary_source, jlpt_level, jlpt_official
                        )
                    VALUES
                        (
                            :run_id, :region_id, 0, :stable_key,
                            0, 2, 'ノマ', 'ノマ', 'ノマ', '名詞',
                            'jmdict-1000060', 'JMdict legacy-v3', NULL, false
                        )
                    RETURNING id
                    """
                ),
                {
                    "run_id": linguistic_run_id,
                    "region_id": region_id,
                    "stable_key": stable_key,
                },
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.linguistic_meanings
                        (token_id, meaning_ordinal, meaning)
                    VALUES (:token_id, 0, 'kanji repetition mark')
                    """
                ),
                {"token_id": token_id},
            )
            call_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.gemini_calls
                        (
                            page_id, job_id, page_call_ordinal, fencing_token,
                            model, prompt_version, schema_version, request_digest,
                            state, reserved_cost, sent_at, finished_at
                        )
                    VALUES
                        (
                            :page_id, :job_id, 1, 1,
                            'legacy-model', 'page-study-v3', 'v1', :request_digest,
                            'succeeded', 0, now(), now()
                        )
                    RETURNING id
                    """
                ),
                {
                    "page_id": page_id,
                    "job_id": job_id,
                    "request_digest": hashlib.sha256(b"legacy-gemini").digest(),
                },
            ).scalar_one()
            analysis_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.gemini_analyses
                        (job_id, linguistic_run_id, gemini_call_id, response_digest)
                    VALUES (:job_id, :run_id, :call_id, :response_digest)
                    RETURNING id
                    """
                ),
                {
                    "job_id": job_id,
                    "run_id": linguistic_run_id,
                    "call_id": call_id,
                    "response_digest": hashlib.sha256(b"legacy-response").digest(),
                },
            ).scalar_one()
            region_analysis_id = connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.gemini_region_analyses
                        (analysis_id, region_id, translation, explanation)
                    VALUES (:analysis_id, :region_id, 'translation', 'explanation')
                    RETURNING id
                    """
                ),
                {"analysis_id": analysis_id, "region_id": region_id},
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO mangasensei.gemini_vocabulary_links
                        (region_analysis_id, token_id)
                    VALUES (:region_analysis_id, :token_id)
                    """
                ),
                {"region_analysis_id": region_analysis_id, "token_id": token_id},
            )

        command.upgrade(config, "head")

        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            lexical = connection.execute(
                text(
                    """
                    SELECT
                        linguistic_run_id, region_id,
                        start_token_ordinal, end_token_ordinal,
                        dictionary_namespace, dictionary_entry_id,
                        form_lemma, form_reading,
                        surface, display_lemma, display_reading
                    FROM mangasensei.lexical_matches
                    """
                )
            ).one()
            meanings = connection.execute(
                text(
                    """
                    SELECT meaning_ordinal, meaning
                    FROM mangasensei.lexical_meanings
                    """
                )
            ).all()
            links = connection.execute(
                text(
                    """
                    SELECT region_analysis_id, lexical_match_id
                    FROM mangasensei.gemini_lexical_vocabulary_links
                    """
                )
            ).all()
            legacy_dictionary_id = connection.execute(
                text(
                    "SELECT dictionary_entry_id FROM mangasensei.linguistic_tokens WHERE id = :id"
                ),
                {"id": token_id},
            ).scalar_one()

        assert revision == _CURRENT_HEAD
        assert lexical == (
            linguistic_run_id,
            region_id,
            0,
            1,
            "JMdict",
            "jmdict-1000060",
            "のま",
            "のま",
            "ノマ",
            "ノマ",
            "ノマ",
        )
        assert meanings == [(0, "kanji repetition mark")]
        assert len(links) == 1
        assert links[0][0] == region_analysis_id
        assert legacy_dictionary_id == "jmdict-1000060"
    finally:
        engine.dispose()
        command.upgrade(config, "head")
