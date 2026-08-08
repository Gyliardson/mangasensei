from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

from mangasensei.linguistics.jmdict_bootstrap import (
    JmdictIntegrityError,
    JmdictManifest,
    JmdictNormalizedArtifact,
    JmdictSourceArtifact,
    convert_simplified_jmdict,
    download_jmdict,
    verify_jmdict,
)

NORMALIZED_FIXTURE = (
    b'{"entries":[{"id":"jmdict-1000000","kanji":[],"meanings":["thanks"],'
    b'"readings":["\xe3\x81\x82\xe3\x82\x8a\xe3\x81\x8c\xe3\x81\xa8\xe3\x81\x86"]},'
    b'{"id":"jmdict-1467640","kanji":["\xe7\x8c\xab"],"meanings":["cat"],'
    b'"readings":["\xe3\x81\xad\xe3\x81\x93"]}],'
    b'"source":{"attribution":"JMdict data provided by EDRDG",'
    b'"license":"CC-BY-SA-4.0",'
    b'"url":"https://example.test/jmdict-eng.json.zip"},'
    b'"version":"jmdict-simplified-3.6.2+test"}'
)


def test_convert_simplified_jmdict_outputs_deterministic_normalized_json() -> None:
    assert convert_simplified_jmdict(
        simplified_payload(),
        version="jmdict-simplified-3.6.2+test",
        language="eng",
        source_url="https://example.test/jmdict-eng.json.zip",
        license_id="CC-BY-SA-4.0",
        attribution="JMdict data provided by EDRDG",
    ) == NORMALIZED_FIXTURE


@pytest.mark.asyncio
async def test_download_jmdict_is_atomic_verified_and_idempotent(tmp_path: Path) -> None:
    source = zipped_source(simplified_payload())
    manifest = manifest_for(source, NORMALIZED_FIXTURE)
    manifest_path = write_manifest(tmp_path, manifest)
    requests = 0

    async def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=source, request=request)

    target = tmp_path / "data" / "jmdict.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        downloaded = await download_jmdict(target, manifest_path=manifest_path, client=client)
        reused = await download_jmdict(target, manifest_path=manifest_path, client=client)

    assert downloaded
    assert not reused
    assert requests == 1
    assert target.read_bytes() == NORMALIZED_FIXTURE
    assert not tuple(target.parent.glob("*.part-*"))
    assert not tuple(target.parent.glob("*.lock"))


@pytest.mark.asyncio
async def test_download_jmdict_rejects_tampered_source_without_output(
    tmp_path: Path,
) -> None:
    expected_source = zipped_source(simplified_payload())
    tampered_source = zipped_source(
        {**simplified_payload(), "words": []}, filename="jmdict-eng.json"
    )
    manifest = manifest_for(expected_source, NORMALIZED_FIXTURE)
    manifest_path = write_manifest(tmp_path, manifest)

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=tampered_source, request=request)

    target = tmp_path / "data" / "jmdict.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(JmdictIntegrityError, match="mismatch"):
            await download_jmdict(target, manifest_path=manifest_path, client=client)

    assert not target.exists()
    assert not tuple(target.parent.glob("*.part-*"))


def test_verify_jmdict_checks_normalized_digest(tmp_path: Path) -> None:
    source = zipped_source(simplified_payload())
    manifest = manifest_for(source, NORMALIZED_FIXTURE)
    target = tmp_path / "jmdict.json"
    target.write_bytes(NORMALIZED_FIXTURE)

    verify_jmdict(target, manifest=manifest)
    target.write_bytes(NORMALIZED_FIXTURE.replace(b"cat", b"dog"))

    with pytest.raises(JmdictIntegrityError, match="checksum mismatch"):
        verify_jmdict(target, manifest=manifest)


def simplified_payload() -> dict[str, object]:
    return {
        "version": "3.6.2",
        "dictDate": "2026-08-03",
        "dictRevisions": ["1.10"],
        "languages": ["eng"],
        "words": [
            {
                "id": "1467640",
                "kanji": [{"text": "猫", "common": True, "tags": []}],
                "kana": [
                    {
                        "text": "ねこ",
                        "common": True,
                        "tags": [],
                        "appliesToKanji": ["*"],
                    }
                ],
                "sense": [
                    {
                        "gloss": [
                            {"lang": "eng", "text": "cat", "gender": None, "type": None},
                            {"lang": "spa", "text": "gato", "gender": None, "type": None},
                        ],
                        "appliesToKanji": ["*"],
                        "appliesToKana": ["*"],
                        "dialect": [],
                        "field": [],
                        "info": [],
                        "languageSource": [],
                        "misc": [],
                        "partOfSpeech": ["n"],
                        "related": [],
                        "antonym": [],
                    }
                ],
            },
            {
                "id": "1000000",
                "kanji": [],
                "kana": [
                    {
                        "text": "ありがとう",
                        "common": True,
                        "tags": [],
                        "appliesToKanji": [],
                    }
                ],
                "sense": [
                    {
                        "gloss": [
                            {"lang": "eng", "text": "thanks", "gender": None, "type": None}
                        ],
                        "appliesToKanji": ["*"],
                        "appliesToKana": ["*"],
                        "dialect": [],
                        "field": [],
                        "info": [],
                        "languageSource": [],
                        "misc": [],
                        "partOfSpeech": ["int"],
                        "related": [],
                        "antonym": [],
                    }
                ],
            },
        ],
    }


def zipped_source(payload: dict[str, object], *, filename: str = "jmdict-eng.json") -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            filename,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    return buffer.getvalue()


def manifest_for(source: bytes, normalized: bytes) -> JmdictManifest:
    return JmdictManifest(
        version="test",
        source=JmdictSourceArtifact(
            filename="jmdict-eng.json.zip",
            url="https://example.test/jmdict-eng.json.zip",
            sha256=hashlib.sha256(source).hexdigest(),
            size_bytes=len(source),
            max_uncompressed_bytes=1_000_000,
            language="eng",
            source_version="jmdict-simplified-3.6.2+test",
            license_id="CC-BY-SA-4.0",
            attribution="JMdict data provided by EDRDG",
            redistribution_status="local-bootstrap-derived-data",
        ),
        normalized=JmdictNormalizedArtifact(
            filename="jmdict.json",
            sha256=hashlib.sha256(normalized).hexdigest(),
            size_bytes=len(normalized),
            entry_count=2,
            converter_version="mangasensei-jmdict-v1",
        ),
    )


def write_manifest(tmp_path: Path, manifest: JmdictManifest) -> Path:
    path = tmp_path / "jmdict_manifest.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return path
