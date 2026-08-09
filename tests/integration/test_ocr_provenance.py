from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select

from mangasensei.api.app import create_app
from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.infrastructure.database.analysis_models import OcrRunRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.service import DictionaryEntry, LinguisticService
from mangasensei.ocr.contracts import OcrProvenance, OcrRegionResult
from mangasensei.ocr.fake import FakeOcrEngine
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker


class TokenizerFixture:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        return ((text, text, text, "名詞"),)


class DictionaryFixture:
    version = "provenance-test-dictionary"
    digest = hashlib.sha256(b"provenance-test-dictionary").digest()

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        del lemma, reading
        return None


def fixture_image() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=(248, 244, 235)).save(output, format="PNG")
    return output.getvalue()


def settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=root,
        model_cache=root / "models",
        capability_peppers=("ocr-provenance-pepper-value-000001",),
    )


async def _persist_with_provenance(
    *,
    database_url: str,
    root: Path,
    provenance: OcrProvenance,
    regions: tuple[OcrRegionResult, ...],
    idempotency_key: str,
) -> OcrRunRecord:
    app = create_app(settings(database_url, root))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": idempotency_key},
            files={"image": ("page.png", fixture_image(), "image/png")},
        )
        assert upload.status_code == 202

    engine, sessions = create_database(database_url)
    worker = Worker(
        sessions=sessions,
        storage=LocalFilesystemStorage(root),
        ocr=FakeOcrEngine(regions=regions, provenance=provenance),
        linguistics=LinguisticService(TokenizerFixture(), DictionaryFixture()),
        gemini=None,
        worker_id="ocr-provenance-worker",
        lease_seconds=60,
    )
    try:
        assert await worker.run_once()
        async with sessions() as session:
            return (await session.execute(select(OcrRunRecord))).scalar_one()
    finally:
        await engine.dispose()


def _assert_provenance(record: OcrRunRecord, expected: OcrProvenance) -> None:
    assert record.detector == expected.detector
    assert record.recognizer == expected.recognizer
    assert record.model_manifest_version == expected.model_manifest_version
    assert record.config_digest == expected.config_digest
    assert record.upstream_repository == expected.upstream_repository
    assert record.upstream_commit == expected.upstream_commit


@pytest.mark.integration
@pytest.mark.asyncio
async def test_worker_persists_exact_engine_provenance(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    provenance = OcrProvenance(
        detector="fixture-detector-v2",
        recognizer="fixture-recognizer-v3",
        model_manifest_version="fixture-manifest-v17",
        config_digest=hashlib.sha256(b"fixture-config-v17").digest(),
        upstream_repository="https://example.invalid/upstream/fixture-ocr",
        upstream_commit="fixture-upstream-commit-v17",
    )
    dimensions = PageDimensions(width=80, height=120)
    bbox = BoundingBox(x=10, y=20, width=40, height=60)
    region = OcrRegionResult(
        id="5ca22b32-6834-59db-a183-428a557a22e8",
        dimensions=dimensions,
        bbox=bbox,
        normalized_bbox=bbox.normalize(dimensions),
        polygon=((10, 20), (50, 20), (50, 80), (10, 80)),
        angle=0.0,
        confidence=0.97,
        japanese_text="猫",
        reading_order=0,
        detector="deliberately-not-the-run-detector",
        recognizer="deliberately-not-the-run-recognizer",
        upstream_commit="deliberately-not-the-run-commit",
    )

    record = await _persist_with_provenance(
        database_url=clean_postgres_url,
        root=tmp_path,
        provenance=provenance,
        regions=(region,),
        idempotency_key="ocr-provenance-nonempty-0001",
    )

    _assert_provenance(record, provenance)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_zero_region_run_still_persists_exact_engine_provenance(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    provenance = OcrProvenance(
        detector="empty-detector-v4",
        recognizer="empty-recognizer-v5",
        model_manifest_version="empty-manifest-v6",
        config_digest=hashlib.sha256(b"empty-config-v6").digest(),
        upstream_repository="https://example.invalid/upstream/empty-ocr",
        upstream_commit="empty-upstream-commit-v6",
    )

    record = await _persist_with_provenance(
        database_url=clean_postgres_url,
        root=tmp_path,
        provenance=provenance,
        regions=(),
        idempotency_key="ocr-provenance-empty-0001",
    )

    _assert_provenance(record, provenance)
