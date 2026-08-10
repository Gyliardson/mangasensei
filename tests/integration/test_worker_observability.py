from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryLookupResult, LinguisticService
from mangasensei.ocr.contracts import OcrImage, OcrResult
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker


def _fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


class _FailingOcr:
    def __init__(self, sensitive_message: str) -> None:
        self._sensitive_message = sensitive_message

    async def analyze(self, image: OcrImage) -> OcrResult:
        del image
        try:
            raise ValueError(self._sensitive_message)
        except ValueError as cause:
            raise RuntimeError("outer private failure detail") from cause


class _Tokenizer:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        del text
        return ()


class _Dictionary:
    version = "observability-test"
    digest = hashlib.sha256(version.encode()).digest()

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        del lemma, reading
        return DictionaryLookupResult.from_candidates(())


class _RecordHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_failure_log_is_useful_and_does_not_expose_sensitive_content(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=clean_postgres_url,
        storage_root=tmp_path,
        capability_peppers=("observability-test-pepper-000000000001",),
    )
    app = create_app(settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "worker-observability-upload-0001"},
            files={"image": ("page.png", _fixture_image(), "image/png")},
        )
    assert upload.status_code == 202
    upload_data = upload.json()["data"]

    sensitive_values = (
        upload_data["capabilities"]["readPage"],
        "SENSITIVE_API_KEY_MARKER",
        clean_postgres_url,
        "IMAGE_BYTES_SECRET_MARKER",
        "秘密のOCR本文",
    )
    sensitive_message = " | ".join(sensitive_values)

    engine, sessions = create_database(clean_postgres_url)
    worker = Worker(
        sessions=sessions,
        storage=LocalFilesystemStorage(tmp_path),
        ocr=_FailingOcr(sensitive_message),
        linguistics=LinguisticService(_Tokenizer(), _Dictionary()),
        gemini=None,
        worker_id="observability-worker",
        lease_seconds=60,
    )

    logger = logging.getLogger("mangasensei.workers.runner")
    handler = _RecordHandler()
    original_level = logger.level
    original_disabled = logger.disabled
    logger.disabled = False
    logger.setLevel(logging.ERROR)
    logger.addHandler(handler)
    try:
        assert await worker.run_once()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)
        logger.disabled = original_disabled

    async with sessions() as session:
        job = (
            await session.execute(
                select(JobRecord).where(JobRecord.public_id == UUID(upload_data["jobId"]))
            )
        ).scalar_one()

    assert job.status == "retryable_failure"
    assert job.error_code == "processing_failed"
    assert job.error_detail == "O processamento falhou sem expor conteúdo sensível."

    records = [
        record
        for record in handler.records
        if record.name == "mangasensei.workers.runner"
        and "worker_pipeline_failed" in record.getMessage()
    ]
    assert len(records) == 1
    record = records[0]
    message = record.getMessage()
    assert record.exc_info is None
    assert "stage=ocr" in message
    assert f"job_id={job.id}" in message
    assert f"attempt_no={job.attempt_count}" in message
    assert f"fencing_token={job.fencing_token}" in message
    assert "error_code=processing_failed" in message
    assert "exception_type=RuntimeError" in message
    assert "traceback=RuntimeError@" in message
    assert "ValueError@" in message
    assert "test_worker_observability.py:" in message
    assert "outer private failure detail" not in message
    for sensitive_value in sensitive_values:
        assert sensitive_value not in message

    await engine.dispose()
