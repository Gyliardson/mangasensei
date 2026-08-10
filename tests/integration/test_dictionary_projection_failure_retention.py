from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from mangasensei.api.app import create_app
from mangasensei.infrastructure.database.dictionary_projection_models import (
    DictionaryProjectionItemRecord,
    DictionaryProjectionMeaningRecord,
    DictionaryProjectionRecord,
    DictionaryProjectionRequestRecord,
    DictionaryProjectionSourceRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.linguistics.jmdict_glosses import LocalizedJmdictGlossResolver
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.dictionary_projection import DictionaryProjectionWorker
from mangasensei.workers.retention import RetentionJanitor
from tests.integration.test_dictionary_projection_flow import (
    CountingOcr,
    CountingTokenizer,
    EnglishDictionary,
    _image,
    _settings,
)
from mangasensei.linguistics.service import LinguisticService


class _FailingProvider:
    def is_supported_language(self, language: str) -> bool:
        return language in {"en", "de"}

    def get_pack(self, language: str) -> object:
        raise LookupError(f"fixture pack unavailable: {language}")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_dictionary_projection_keeps_prior_result_and_retention_cascades(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    app = create_app(_settings(clean_postgres_url, tmp_path))
    ocr = CountingOcr()
    tokenizer = CountingTokenizer()
    engine, sessions = create_database(clean_postgres_url)
    worker = DictionaryProjectionWorker(
        sessions=sessions,
        storage=LocalFilesystemStorage(tmp_path),
        ocr=ocr,
        linguistics=LinguisticService(tokenizer, EnglishDictionary()),
        gemini=None,
        worker_id="dictionary-failure-worker",
        lease_seconds=60,
        gloss_resolver=LocalizedJmdictGlossResolver(_FailingProvider()),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "key-a"},
            files={
                "image": ("page.png", _image(), "image/png"),
                "studyLanguage": (None, "en"),
            },
        )
        assert upload.status_code == 202
        data = upload.json()["data"]
        assert await worker.run_once()

        before = (
            await client.get(
                f"/api/v1/pages/{data['pageId']}",
                headers={"X-Page-Token": data["capabilities"]["readPage"]},
            )
        ).json()["data"]
        assert before["resultAvailable"] is True
        assert before["requestedDictionaryLanguage"] == "en"

        request = await client.post(
            f"/api/v1/pages/{data['pageId']}/reprocess",
            headers={
                "X-Page-Token": data["capabilities"]["reprocessPage"],
                "Idempotency-Key": "key-b",
            },
            json={"dictionaryLanguage": "de"},
        )
        assert request.status_code == 202
        assert await worker.run_once()

        after_failure = (
            await client.get(
                f"/api/v1/pages/{data['pageId']}",
                headers={"X-Page-Token": data["capabilities"]["readPage"]},
            )
        ).json()["data"]
        assert after_failure["resultAvailable"] is True
        assert after_failure["requestedDictionaryLanguage"] == "en"
        assert after_failure["status"] in {"retryable_failure", "failed"}
        assert ocr.calls == 1
        assert tokenizer.calls == 1

    page_public_id = UUID(data["pageId"])
    async with sessions.begin() as session:
        page = (
            await session.execute(
                select(PageRecord).where(PageRecord.public_id == page_public_id).with_for_update()
            )
        ).scalar_one()
        page.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    janitor = RetentionJanitor(sessions, LocalFilesystemStorage(tmp_path))
    assert await janitor.run_once() == 1

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(PageRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(JobRecord)) == 0
        for model in (
            DictionaryProjectionRequestRecord,
            DictionaryProjectionRecord,
            DictionaryProjectionSourceRecord,
            DictionaryProjectionItemRecord,
            DictionaryProjectionMeaningRecord,
        ):
            assert await session.scalar(select(func.count()).select_from(model)) == 0

    await engine.dispose()
