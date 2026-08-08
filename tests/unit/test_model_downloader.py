from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from mangasensei.ocr.models.downloader import download_artifact
from mangasensei.ocr.models.manifest import ModelArtifact, ModelIntegrityError


def artifact_for(content: bytes) -> ModelArtifact:
    return ModelArtifact(
        filename="fixture.ckpt",
        url="https://models.example.test/fixture.ckpt",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        redistribution_status="local-use-only-pending-rights-review",
    )


@pytest.mark.asyncio
async def test_download_artifact_is_atomic_and_checksum_verified(tmp_path: Path) -> None:
    content = b"reviewed-model-content"
    requests = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=content, request=request)

    target = tmp_path / "models" / "fixture.ckpt"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        downloaded = await download_artifact(artifact_for(content), target, client=client)
        reused = await download_artifact(artifact_for(content), target, client=client)

    assert downloaded
    assert not reused
    assert requests == 1
    assert target.read_bytes() == content
    assert not tuple(target.parent.glob("*.part-*"))


@pytest.mark.asyncio
async def test_download_artifact_rejects_tampered_content(tmp_path: Path) -> None:
    expected = b"expected-model"

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"tampered-model", request=request)

    target = tmp_path / "models" / "fixture.ckpt"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ModelIntegrityError, match="checksum mismatch"):
            await download_artifact(artifact_for(expected), target, client=client)

    assert not target.exists()
    assert not tuple(target.parent.glob("*.part-*"))
