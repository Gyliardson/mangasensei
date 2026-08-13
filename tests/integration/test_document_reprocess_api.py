from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.domain.capabilities import DocumentCapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.analysis_models import (
    GeminiAnalysisRecord,
    GeminiCallRecord,
    LinguisticRunRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.dictionary_projection_models import (
    DictionaryProjectionRequestRecord,
)
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.storage_models import PageRecord
from tests.integration.job_fixture_helpers import (
    advance_pending_job_to_processing_linguistics,
    finish_processing_job,
)
from tests.integration.test_document_api import (
    _add_completed_result,
    _seed_readable_document,
    make_settings,
)

_PEPPER = "integration-test-pepper-value-0001"


async def _complete_initial_page(database_url: str, page_public_id: UUID) -> None:
    async_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    digest = hashlib.sha256(f"completed-{page_public_id}".encode()).digest()
    async with sessions.begin() as session:
        page = (
            await session.execute(select(PageRecord).where(PageRecord.public_id == page_public_id))
        ).scalar_one()
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.page_id == page.id).order_by(JobRecord.id)
            )
        ).scalars().first()
        assert job is not None
        await advance_pending_job_to_processing_linguistics(session, job)
        await _add_completed_result(session, job=job, digest=digest)
        await finish_processing_job(session, job)
    await engine.dispose()


async def _issue_reprocess_token(database_url: str, document_public_id: UUID) -> str:
    async_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    capability_service = CapabilityService((_PEPPER,))
    async with sessions.begin() as session:
        document = (
            await session.execute(
                select(DocumentRecord).where(DocumentRecord.public_id == document_public_id)
            )
        ).scalar_one()
        issued = capability_service.issue(
            resource_id=str(document.public_id),
            scope=DocumentCapabilityScope.REPROCESS_DOCUMENT,
            expires_at=document.expires_at,
        )
        session.add(
            DocumentCapabilityRecord(
                document_id=document.id,
                key_id="v1",
                scope=DocumentCapabilityScope.REPROCESS_DOCUMENT.value,
                digest=bytes.fromhex(issued.persisted_digest),
                expires_at=issued.expires_at,
            )
        )
    await engine.dispose()
    return issued.token


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nested_study_reprocess_requires_scope_membership_and_delegates_contract(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    first = await _seed_readable_document(clean_postgres_url, tmp_path, suffix="reprocess-first")
    second = await _seed_readable_document(clean_postgres_url, tmp_path, suffix="reprocess-second")
    await _complete_initial_page(clean_postgres_url, first.page_id)
    reprocess_token = await _issue_reprocess_token(clean_postgres_url, first.document_id)
    app = create_app(make_settings(clean_postgres_url, tmp_path))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        wrong_scope = await client.post(
            f"/api/v1/documents/{first.document_id}/pages/{first.page_id}/reprocess",
            headers={
                "X-Document-Token": first.read_token,
                "Idempotency-Key": "nested-study-wrong-scope",
            },
            json={"studyLanguage": "pt-BR"},
        )
        nonmember = await client.post(
            f"/api/v1/documents/{first.document_id}/pages/{second.page_id}/reprocess",
            headers={
                "X-Document-Token": reprocess_token,
                "Idempotency-Key": "nested-study-nonmember",
            },
            json={"studyLanguage": "pt-BR"},
        )
        wrong_document = await client.post(
            f"/api/v1/documents/{second.document_id}/pages/{second.page_id}/reprocess",
            headers={
                "X-Document-Token": reprocess_token,
                "Idempotency-Key": "nested-study-wrong-document",
            },
            json={"studyLanguage": "pt-BR"},
        )
        accepted = await client.post(
            f"/api/v1/documents/{first.document_id}/pages/{first.page_id}/reprocess",
            headers={
                "X-Document-Token": reprocess_token,
                "Idempotency-Key": "nested-study-accepted",
            },
            json={"studyLanguage": "pt-BR"},
        )

    assert wrong_scope.status_code == 404
    assert nonmember.status_code == 404
    assert wrong_document.status_code == 404
    assert accepted.status_code == 202
    assert accepted.json()["data"]["studyLanguage"] == "pt-BR"
    assert accepted.json()["data"]["created"] is True

    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        page = (
            await session.execute(select(PageRecord).where(PageRecord.public_id == first.page_id))
        ).scalar_one()
        jobs = tuple(
            (
                await session.execute(
                    select(JobRecord).where(JobRecord.page_id == page.id).order_by(JobRecord.id)
                )
            ).scalars()
        )
    assert len(jobs) == 2
    assert jobs[-1].job_kind == "study_language_reprocess"
    assert jobs[-1].study_language == "pt-BR"
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nested_dictionary_reprojection_creates_no_ocr_sudachi_or_gemini_work(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    seeded = await _seed_readable_document(
        clean_postgres_url,
        tmp_path,
        suffix="dictionary-reprocess",
    )
    await _complete_initial_page(clean_postgres_url, seeded.page_id)
    reprocess_token = await _issue_reprocess_token(clean_postgres_url, seeded.document_id)
    async_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        before = {
            "ocr": await session.scalar(select(func.count()).select_from(OcrRunRecord)),
            "linguistic": await session.scalar(
                select(func.count()).select_from(LinguisticRunRecord)
            ),
            "gemini_calls": await session.scalar(
                select(func.count()).select_from(GeminiCallRecord)
            ),
            "gemini_analyses": await session.scalar(
                select(func.count()).select_from(GeminiAnalysisRecord)
            ),
        }

    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/documents/{seeded.document_id}/pages/{seeded.page_id}/reprocess",
            headers={
                "X-Document-Token": reprocess_token,
                "Idempotency-Key": "nested-dictionary-reprojection",
            },
            json={"dictionaryLanguage": "en"},
        )
        unsupported = await client.post(
            f"/api/v1/documents/{seeded.document_id}/pages/{seeded.page_id}/reprocess",
            headers={
                "X-Document-Token": reprocess_token,
                "Idempotency-Key": "nested-dictionary-unsupported",
            },
            json={"dictionaryLanguage": "de"},
        )

    assert response.status_code == 202
    assert response.json()["data"]["requestedDictionaryLanguage"] == "en"
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "invalid_request"
    async with sessions() as session:
        page = (
            await session.execute(select(PageRecord).where(PageRecord.public_id == seeded.page_id))
        ).scalar_one()
        projection_job = (
            await session.execute(
                select(JobRecord)
                .where(JobRecord.page_id == page.id)
                .order_by(JobRecord.id.desc())
                .limit(1)
            )
        ).scalar_one()
        request = await session.get(DictionaryProjectionRequestRecord, projection_job.id)
        projection_request_count = await session.scalar(
            select(func.count()).select_from(DictionaryProjectionRequestRecord)
        )
        after = {
            "ocr": await session.scalar(select(func.count()).select_from(OcrRunRecord)),
            "linguistic": await session.scalar(
                select(func.count()).select_from(LinguisticRunRecord)
            ),
            "gemini_calls": await session.scalar(
                select(func.count()).select_from(GeminiCallRecord)
            ),
            "gemini_analyses": await session.scalar(
                select(func.count()).select_from(GeminiAnalysisRecord)
            ),
        }

    assert projection_job.job_kind == "dictionary_language_reprocess"
    assert request is not None
    assert request.requested_dictionary_language == "en"
    assert projection_request_count == 1
    assert after == before
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_nested_reprocess_requires_exactly_one_language_axis(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    seeded = await _seed_readable_document(clean_postgres_url, tmp_path, suffix="invalid-axis")
    reprocess_token = await _issue_reprocess_token(clean_postgres_url, seeded.document_id)
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    url = f"/api/v1/documents/{seeded.document_id}/pages/{seeded.page_id}/reprocess"
    headers = {
        "X-Document-Token": reprocess_token,
        "Idempotency-Key": "nested-invalid-axis",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        neither = await client.post(url, headers=headers, json={})
        both = await client.post(
            url,
            headers={**headers, "Idempotency-Key": "nested-invalid-axis-both"},
            json={"studyLanguage": "en", "dictionaryLanguage": "en"},
        )

    assert neither.status_code == 422
    assert neither.json()["error"]["code"] == "invalid_request"
    assert both.status_code == 422
    assert both.json()["error"]["code"] == "invalid_request"
