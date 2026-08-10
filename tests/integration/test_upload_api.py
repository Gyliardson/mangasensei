from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mangasensei.api.app import create_app
from mangasensei.application.uploads import UploadService
from mangasensei.config import Settings
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.retention import RetentionJanitor


def page_image(color: tuple[int, int, int] = (240, 235, 225)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (80, 120), color=color).save(output, format="PNG")
    return output.getvalue()


def make_settings(database_url: str, storage_root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url,
        storage_root=storage_root,
        model_cache=storage_root / "models",
        capability_peppers=("integration-test-pepper-value-0001",),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_is_idempotent_and_original_download_is_byte_exact(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    original = page_image()
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "upload-integration-0001"},
            files={"image": ("pagina.png", original, "image/png")},
        )
        replay = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "upload-integration-0001"},
            files={"image": ("pagina.png", original, "image/png")},
        )

        assert first.status_code == 202
        assert replay.status_code == 200
        first_data = first.json()["data"]
        replay_data = replay.json()["data"]
        assert first_data["pageId"] == replay_data["pageId"]
        assert first_data["contentSha256"] == hashlib.sha256(original).hexdigest()

        image_response = await client.get(
            f"/api/v1/pages/{first_data['pageId']}/image",
            headers={"X-Page-Token": first_data["capabilities"]["readImage"]},
        )
        assert image_response.status_code == 200
        assert image_response.content == original
        assert image_response.headers["cache-control"] == "private, no-store"
        assert image_response.headers["x-content-type-options"] == "nosniff"

        denied = await client.get(
            f"/api/v1/pages/{first_data['pageId']}/image",
            headers={"X-Page-Token": "invalid-token"},
        )
        assert denied.status_code == 404
        assert "detail" not in denied.json()["error"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_failed_upload_persistence_is_reconciled_without_storage_orphan(
    clean_postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = page_image((25, 35, 45))

    async def fail_after_rows_are_staged(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("injected persistence failure")

    monkeypatch.setattr(UploadService, "_issue_capabilities", fail_after_rows_are_staged)
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        failed = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "upload-persistence-failure-0001"},
            files={"image": ("failure.png", original, "image/png")},
        )

    assert failed.status_code == 500

    storage = LocalFilesystemStorage(tmp_path)
    pending = await storage.pending_writes()
    assert len(pending) == 1
    assert await storage.read(pending[0].storage_key) == original

    database_url = clean_postgres_url.replace("postgresql://", "postgresql+psycopg://", 1)
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        assert (await session.execute(select(ImageBlobRecord))).scalars().all() == []
        assert (await session.execute(select(PageRecord))).scalars().all() == []

    reconciled = await RetentionJanitor(sessions, storage).run_once(batch_size=10)

    assert reconciled == 0
    assert await storage.pending_writes() == ()
    with pytest.raises(FileNotFoundError):
        await storage.read(pending[0].storage_key)
    async with sessions() as session:
        assert (await session.execute(select(ImageBlobRecord))).scalars().all() == []
        assert (await session.execute(select(PageRecord))).scalars().all() == []
    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_idempotency_key_with_different_image_conflicts(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "upload-integration-conflict"},
            files={"image": ("one.png", page_image(), "image/png")},
        )
        conflict = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "upload-integration-conflict"},
            files={"image": ("two.png", page_image((20, 30, 40)), "image/png")},
        )

    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_operational_endpoints_and_static_frontend(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    frontend = tmp_path / "frontend"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text(
        '<!doctype html><html lang="pt-BR"><title>MangaSensei</title></html>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("export {};", encoding="utf-8")
    application_settings = make_settings(clean_postgres_url, tmp_path).model_copy(
        update={"frontend_dist": frontend}
    )
    app = create_app(application_settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health = await client.get("/health")
        ready = await client.get("/ready")
        metrics = await client.get("/metrics")
        index = await client.get("/")
        asset = await client.get("/assets/app.js")
        missing_api = await client.get("/api/v1/pages/not-a-uuid")

    assert health.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["data"]["databaseRevision"] == "e2f6a0c84b11"
    assert metrics.status_code == 200
    assert "mangasensei_http_requests_total" in metrics.text
    assert index.status_code == 200
    assert "MangaSensei" in index.text
    assert asset.status_code == 200
    assert missing_api.status_code == 422


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_rate_limit_is_shared_and_returns_retry_after(
    clean_postgres_url: str, tmp_path: Path
) -> None:
    application_settings = make_settings(clean_postgres_url, tmp_path).model_copy(
        update={"upload_rate_limit_per_minute": 1, "frontend_dist": None}
    )
    app = create_app(application_settings)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "rate-limit-upload-0001"},
            files={"image": ("one.png", page_image(), "image/png")},
        )
        limited = await client.post(
            "/api/v1/pages",
            headers={"Idempotency-Key": "rate-limit-upload-0002"},
            files={"image": ("two.png", page_image((10, 20, 30)), "image/png")},
        )

    assert first.status_code == 202
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
