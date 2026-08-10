from __future__ import annotations

import io
import time
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from mangasensei.api.app import create_app
from tests.integration.test_document_api import make_settings


def _tiny_page() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 48), color=(245, 242, 236)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_document_scale_near_configured_page_limit_reports_creation_query_and_payload_metrics(
    clean_postgres_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = _tiny_page()
    total_pages = 200
    app = create_app(make_settings(clean_postgres_url, tmp_path))
    files = [
        ("images[]", (f"page-{ordinal:03}.png", page, "image/png"))
        for ordinal in range(total_pages)
    ]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_started = time.perf_counter()
        created = await client.post(
            "/api/v1/documents",
            headers={"Idempotency-Key": "document-scale-200-pages"},
            data={"studyLanguage": "en"},
            files=files,
        )
        create_seconds = time.perf_counter() - create_started
        assert created.status_code == 202
        data = created.json()["data"]

        query_started = time.perf_counter()
        aggregate = await client.get(
            f"/api/v1/documents/{data['documentId']}",
            headers={"X-Document-Token": data["capabilities"]["readDocument"]},
        )
        query_seconds = time.perf_counter() - query_started

    assert aggregate.status_code == 200
    assert data["progress"]["totalPages"] == total_pages
    assert len(data["pages"]) == total_pages
    assert aggregate.json()["data"]["progress"]["processingPages"] == total_pages
    assert len(aggregate.content) < 100_000
    with capsys.disabled():
        print(
            "slice_b_scale_metrics "
            f"pages={total_pages} "
            f"create_seconds={create_seconds:.6f} "
            f"aggregate_query_seconds={query_seconds:.6f} "
            f"create_payload_bytes={len(created.content)} "
            f"aggregate_payload_bytes={len(aggregate.content)}"
        )
