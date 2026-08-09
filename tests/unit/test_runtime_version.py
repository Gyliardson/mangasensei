from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import mangasensei
from mangasensei.api.app import create_app
from mangasensei.config import Settings


@pytest.mark.asyncio
async def test_api_metadata_and_health_derive_package_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    synthetic_version = "9.8.7"
    monkeypatch.setattr(mangasensei, "__version__", synthetic_version)
    app = create_app(
        Settings(
            environment="test",
            database_url="postgresql+psycopg://mangasensei:mangasensei@localhost:5432/mangasensei",
            storage_root=tmp_path,
            capability_peppers=("version-test-capability-pepper-000000000001",),
        )
    )

    assert app.version == synthetic_version
    assert app.openapi()["info"]["version"] == synthetic_version

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["version"] == synthetic_version
