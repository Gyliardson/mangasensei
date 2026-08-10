from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.infrastructure.database.dictionary_projection_models import (
    DictionaryProjectionRecord,
    DictionaryProjectionSourceRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from tests.integration.test_document_api import (
    _add_completed_result,
    _seed_readable_document,
    make_settings,
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_authorized_child_page_exposes_dictionary_projection_contract(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    seeded = await _seed_readable_document(
        clean_postgres_url,
        tmp_path,
        suffix="dictionary-projection",
    )
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    digest = hashlib.sha256(b"document-dictionary-projection").digest()
    german_digest = hashlib.sha256(b"document-dictionary-projection-de").digest()

    async with sessions.begin() as session:
        page = (
            await session.execute(
                select(PageRecord).where(PageRecord.public_id == seeded.page_id)
            )
        ).scalar_one()
        analysis_job = JobRecord(
            page_id=page.id,
            job_kind="page_analysis",
            study_language="en",
            idempotency_digest=hashlib.sha256(b"document-dictionary-analysis").digest(),
            request_digest=page.request_digest,
            status="completed",
            finished_at=datetime.now(UTC),
        )
        session.add(analysis_job)
        await session.flush()
        await _add_completed_result(session, job=analysis_job, digest=digest)
        study_result = await session.get_one(StudyResultRecord, analysis_job.id)

        projection_job = JobRecord(
            page_id=page.id,
            job_kind="dictionary_language_reprocess",
            study_language="en",
            idempotency_digest=hashlib.sha256(b"document-dictionary-reprojection").digest(),
            request_digest=page.request_digest,
            status="completed",
            finished_at=datetime.now(UTC),
        )
        session.add(projection_job)
        await session.flush()
        session.add(
            DictionaryProjectionRecord(
                job_id=projection_job.id,
                linguistic_run_id=study_result.linguistic_run_id,
                requested_dictionary_language="de",
                fallback_dictionary_language="en",
            )
        )
        await session.flush()
        source_ref = f"jmdict:de:test:{german_digest.hex()[:16]}"
        session.add(
            DictionaryProjectionSourceRecord(
                projection_job_id=projection_job.id,
                source_ref=source_ref,
                dataset="JMdict",
                product_language="de",
                source_version="test",
                normalized_digest=german_digest,
            )
        )

    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/documents/{seeded.document_id}/pages/{seeded.page_id}",
            headers={"X-Document-Token": seeded.read_token},
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "completed"
    assert data["resultAvailable"] is True
    assert data["dictionaryLanguage"] == "en"
    assert data["requestedDictionaryLanguage"] == "de"
    assert data["fallbackDictionaryLanguage"] == "en"
    assert data["dictionarySources"] == [
        {
            "ref": source_ref,
            "dataset": "JMdict",
            "productLanguage": "de",
            "sourceVersion": "test",
            "normalizedDigestSha256": german_digest.hex(),
        }
    ]
    assert data["regions"] == []
    await engine.dispose()
