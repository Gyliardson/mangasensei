from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.jmdict_bootstrap import (
    CONVERTER_VERSION,
    JmdictIntegrityError,
    JmdictManifest,
    JmdictNormalizedArtifact,
    JmdictSourceArtifact,
    convert_simplified_jmdict,
    download_jmdict,
    verify_jmdict,
)


def test_convert_simplified_jmdict_outputs_deterministic_normalized_json() -> None:
    first = converted_fixture(simplified_payload())
    second = converted_fixture(simplified_payload())

    assert first == second
    payload = json.loads(first)
    assert payload == {
        "converterVersion": CONVERTER_VERSION,
        "entries": [
            {
                "forms": [
                    {"lemma": "ありがとう", "meanings": ["thanks"], "reading": "ありがとう"}
                ],
                "id": "jmdict-1000000",
            },
            {
                "forms": [
                    {"lemma": "ねこ", "meanings": ["cat"], "reading": "ねこ"},
                    {"lemma": "猫", "meanings": ["cat"], "reading": "ねこ"},
                ],
                "id": "jmdict-1467640",
            },
        ],
        "source": {
            "attribution": "JMdict data provided by EDRDG",
            "license": "CC-BY-SA-4.0",
            "url": "https://example.test/jmdict-eng.json.zip",
        },
        "version": "jmdict-simplified-3.6.2+test",
    }


def test_actual_pinned_restricted_entry_preserves_reading_and_sense_rules(
    tmp_path: Path,
) -> None:
    """Entry 1010230 was verified against the manifest-pinned 2026-08-03 source ZIP."""
    dictionary_path = tmp_path / "jmdict.json"
    dictionary_path.write_bytes(converted_fixture(actual_restricted_payload()))
    dictionary = JsonJmdictDictionary(dictionary_path)

    hanpen = dictionary.lookup("半片", "ハンペン")
    hanpei = dictionary.lookup("半平", "ハンペイ")

    assert hanpen is not None
    assert hanpen.meanings == (
        "pounded fish cake",
        "half a slice",
        "half a ticket",
        "ticket stub",
    )
    assert hanpei is not None
    assert hanpei.meanings == ("pounded fish cake",)
    assert dictionary.lookup("半片", "ハンペイ") is None
    assert dictionary.lookup("半平", "ハンペン") is not None


def test_kana_only_and_unrestricted_forms_remain_supported(tmp_path: Path) -> None:
    dictionary_path = tmp_path / "jmdict.json"
    dictionary_path.write_bytes(converted_fixture(simplified_payload()))
    dictionary = JsonJmdictDictionary(dictionary_path)

    kana_only = dictionary.lookup("ありがとう", "アリガトウ")
    cat = dictionary.lookup("猫", "ネコ")

    assert kana_only is not None
    assert kana_only.meanings == ("thanks",)
    assert cat is not None
    assert cat.meanings == ("cat",)


@pytest.mark.asyncio
async def test_download_jmdict_is_atomic_verified_and_idempotent(tmp_path: Path) -> None:
    normalized = converted_fixture(simplified_payload())
    source = zipped_source(simplified_payload())
    manifest = manifest_for(source, normalized)
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
    assert target.read_bytes() == normalized
    assert not tuple(target.parent.glob("*.part-*"))
    assert not tuple(target.parent.glob("*.lock"))


@pytest.mark.asyncio
async def test_download_jmdict_rejects_tampered_source_without_output(
    tmp_path: Path,
) -> None:
    normalized = converted_fixture(simplified_payload())
    expected_source = zipped_source(simplified_payload())
    tampered_source = zipped_source(
        {**simplified_payload(), "words": []}, filename="jmdict-eng.json"
    )
    manifest = manifest_for(expected_source, normalized)
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
    normalized = converted_fixture(simplified_payload())
    source = zipped_source(simplified_payload())
    manifest = manifest_for(source, normalized)
    target = tmp_path / "jmdict.json"
    target.write_bytes(normalized)

    verify_jmdict(target, manifest=manifest)
    target.write_bytes(normalized.replace(b"cat", b"dog"))

    with pytest.raises(JmdictIntegrityError, match="mismatch"):
        verify_jmdict(target, manifest=manifest)


def test_converter_rejects_restriction_referencing_unknown_form() -> None:
    payload = actual_restricted_payload()
    word = payload["words"][0]
    assert isinstance(word, dict)
    kana = word["kana"]
    assert isinstance(kana, list)
    restricted = kana[1]
    assert isinstance(restricted, dict)
    restricted["appliesToKanji"] = ["不存在"]

    with pytest.raises(JmdictIntegrityError, match="unknown form"):
        converted_fixture(payload)


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
                "sense": [sense(["cat"])],
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
                "sense": [sense(["thanks"])],
            },
        ],
    }


def actual_restricted_payload() -> dict[str, object]:
    # Exact form/restriction/gloss fields from source entry 1010230, verified by
    # JMdict restriction investigation #1 against source SHA-256
    # 1806d2817215ebe7ded997c8dac4831a3335d83ed12f321ac869a97e745d3a5c.
    return {
        "words": [
            {
                "id": "1010230",
                "kanji": [
                    {"text": "半片", "common": False, "tags": []},
                    {"text": "半平", "common": False, "tags": []},
                ],
                "kana": [
                    {
                        "text": "はんぺん",
                        "common": True,
                        "tags": [],
                        "appliesToKanji": ["*"],
                    },
                    {
                        "text": "はんぺい",
                        "common": False,
                        "tags": [],
                        "appliesToKanji": ["半平"],
                    },
                ],
                "sense": [
                    sense(["pounded fish cake"]),
                    sense(
                        ["half a slice", "half a ticket", "ticket stub"],
                        applies_to_kanji=["半片"],
                    ),
                ],
            }
        ]
    }


def sense(
    glosses: list[str],
    *,
    applies_to_kanji: list[str] | None = None,
    applies_to_kana: list[str] | None = None,
) -> dict[str, object]:
    return {
        "gloss": [
            {"lang": "eng", "text": text, "gender": None, "type": None}
            for text in glosses
        ],
        "appliesToKanji": applies_to_kanji or ["*"],
        "appliesToKana": applies_to_kana or ["*"],
        "dialect": [],
        "field": [],
        "info": [],
        "languageSource": [],
        "misc": [],
        "partOfSpeech": ["n"],
        "related": [],
        "antonym": [],
    }


def converted_fixture(payload: dict[str, object]) -> bytes:
    return convert_simplified_jmdict(
        payload,
        version="jmdict-simplified-3.6.2+test",
        language="eng",
        source_url="https://example.test/jmdict-eng.json.zip",
        license_id="CC-BY-SA-4.0",
        attribution="JMdict data provided by EDRDG",
    )


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
            entry_count=len(json.loads(normalized)["entries"]),
            converter_version=CONVERTER_VERSION,
        ),
    )


def write_manifest(tmp_path: Path, manifest: JmdictManifest) -> Path:
    path = tmp_path / "jmdict_manifest.json"
    path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return path
