from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from mangasensei.api.app import create_app
from mangasensei.infrastructure.database.analysis_models import (
    GeminiCallRecord,
    LinguisticRunRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.dictionary_projection_models import (
    DictionaryProjectionItemRecord,
    DictionaryProjectionRecord,
    DictionaryProjectionRequestRecord,
    DictionaryProjectionSourceRecord,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.lexical_models import LexicalMatchRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from tests.integration.test_dictionary_projection_flow import (
    CountingOcr,
    CountingTokenizer,
    _image,
    _Provider,
    _settings,
    _worker,
)


def _identity(record: LexicalMatchRecord) -> tuple[str, str, str, str, bytes]:
    return (
        record.dictionary_namespace,
        record.dictionary_entry_id,
        record.form_lemma,
        record.form_reading,
        record.stable_key,
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_preupgrade_pending_german_projection_completes_via_english_fallback_safely(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    """Upgrade-safe legacy work uses English fallback without acquiring German data."""
    app = create_app(_settings(clean_postgres_url, tmp_path))
    ocr = CountingOcr()
    tokenizer = CountingTokenizer()
    provider = _Provider()
    worker, worker_engine = _worker(
        clean_postgres_url,
        tmp_path,
        ocr=ocr,
        tokenizer=tokenizer,
        provider=provider,
    )
    inspection_engine, sessions = create_database(clean_postgres_url)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "legacy-queue-upgrade-upload"},
            files={
                "image": ("page.png", _image(), "image/png"),
                "studyLanguage": (None, "en"),
            },
        )
        assert upload.status_code == 202
        upload_data = upload.json()["data"]
        assert await worker.run_once()

        baseline = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert baseline.status_code == 200
        baseline_data = baseline.json()["data"]
        assert baseline_data["resultAvailable"] is True
        assert baseline_data["requestedDictionaryLanguage"] == "en"
        baseline_vocabulary = baseline_data["regions"][0]["vocabulary"]
        assert baseline_vocabulary

        async with sessions.begin() as session:
            page = (
                await session.execute(
                    select(PageRecord).where(PageRecord.public_id == UUID(upload_data["pageId"]))
                )
            ).scalar_one()
            result = (
                await session.execute(
                    select(StudyResultRecord).order_by(StudyResultRecord.id.desc())
                )
            ).scalars().first()
            assert result is not None
            linguistic_run_id = result.linguistic_run_id
            identities_before = tuple(
                _identity(record)
                for record in (
                    await session.execute(
                        select(LexicalMatchRecord)
                        .where(LexicalMatchRecord.linguistic_run_id == linguistic_run_id)
                        .order_by(LexicalMatchRecord.id)
                    )
                ).scalars()
            )
            counts_before = {
                "ocr": await session.scalar(select(func.count()).select_from(OcrRunRecord)),
                "linguistic": await session.scalar(
                    select(func.count()).select_from(LinguisticRunRecord)
                ),
                "gemini": await session.scalar(select(func.count()).select_from(GeminiCallRecord)),
            }
            legacy_job = JobRecord(
                page_id=page.id,
                job_kind="dictionary_language_reprocess",
                study_language=result.study_language,
                idempotency_digest=hashlib.sha256(b"legacy-de-projection").digest(),
                request_digest=page.request_digest,
            )
            session.add(legacy_job)
            await session.flush()
            session.add(
                DictionaryProjectionRequestRecord(
                    job_id=legacy_job.id,
                    requested_dictionary_language="de",
                )
            )
            legacy_job_id = legacy_job.id

        pending = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert pending.status_code == 200
        pending_data = pending.json()["data"]
        assert pending_data["resultAvailable"] is True
        assert pending_data["requestedDictionaryLanguage"] == "en"
        assert pending_data["regions"][0]["vocabulary"] == baseline_vocabulary

        loads_before_legacy = tuple(provider.loads)
        assert await worker.run_once()
        assert not await worker.run_once()

        completed = await client.get(
            f"/api/v1/pages/{upload_data['pageId']}",
            headers={"X-Page-Token": upload_data["capabilities"]["readPage"]},
        )
        assert completed.status_code == 200
        completed_data = completed.json()["data"]
        assert completed_data["resultAvailable"] is True
        assert completed_data["requestedDictionaryLanguage"] == "de"
        assert completed_data["fallbackDictionaryLanguage"] == "en"
        assert {
            source["productLanguage"] for source in completed_data["dictionarySources"]
        } == {"en"}
        projected_vocabulary = completed_data["regions"][0]["vocabulary"]
        assert projected_vocabulary
        assert all(
            item["effectiveLanguage"] == "en"
            and item["fallbackUsed"] is True
            and item["fallbackReason"] == "unsupported_requested_language"
            for item in projected_vocabulary
        )

    async with sessions() as session:
        job = await session.get(JobRecord, legacy_job_id)
        request = await session.get(DictionaryProjectionRequestRecord, legacy_job_id)
        projection = await session.get(DictionaryProjectionRecord, legacy_job_id)
        attempts = tuple(
            (
                await session.execute(
                    select(JobAttemptRecord)
                    .where(JobAttemptRecord.job_id == legacy_job_id)
                    .order_by(JobAttemptRecord.attempt_no)
                )
            ).scalars()
        )
        sources = tuple(
            (
                await session.execute(
                    select(DictionaryProjectionSourceRecord).where(
                        DictionaryProjectionSourceRecord.projection_job_id == legacy_job_id
                    )
                )
            ).scalars()
        )
        items = tuple(
            (
                await session.execute(
                    select(DictionaryProjectionItemRecord).where(
                        DictionaryProjectionItemRecord.projection_job_id == legacy_job_id
                    )
                )
            ).scalars()
        )
        identities_after = tuple(
            _identity(record)
            for record in (
                await session.execute(
                    select(LexicalMatchRecord)
                    .where(LexicalMatchRecord.linguistic_run_id == linguistic_run_id)
                    .order_by(LexicalMatchRecord.id)
                )
            ).scalars()
        )
        counts_after = {
            "ocr": await session.scalar(select(func.count()).select_from(OcrRunRecord)),
            "linguistic": await session.scalar(
                select(func.count()).select_from(LinguisticRunRecord)
            ),
            "gemini": await session.scalar(select(func.count()).select_from(GeminiCallRecord)),
        }

    assert job is not None
    assert job.status == "completed"
    assert job.attempt_count == 1
    assert job.fencing_token == 1
    assert job.worker_id is None
    assert job.finished_at is not None
    assert request is not None
    assert request.requested_dictionary_language == "de"
    assert projection is not None
    assert projection.requested_dictionary_language == "de"
    assert projection.fallback_dictionary_language == "en"
    assert len(attempts) == 1
    assert attempts[0].fencing_token == job.fencing_token
    assert attempts[0].outcome == "completed_dictionary_projection"
    assert attempts[0].ended_at is not None
    assert sources
    assert {source.product_language for source in sources} == {"en"}
    assert items
    assert all(
        item.effective_dictionary_language == "en"
        and item.fallback_used is True
        and item.fallback_reason == "unsupported_requested_language"
        for item in items
    )
    assert identities_after == identities_before
    assert counts_after == counts_before
    assert ocr.calls == 1
    assert tokenizer.calls == 1
    assert provider.loads[: len(loads_before_legacy)] == list(loads_before_legacy)
    assert provider.loads[len(loads_before_legacy) :]
    assert set(provider.loads[len(loads_before_legacy) :]) == {"en"}
    assert "de" not in provider.loads

    await inspection_engine.dispose()
    await worker_engine.dispose()
