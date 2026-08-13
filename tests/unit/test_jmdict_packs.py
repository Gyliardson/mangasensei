from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

from mangasensei.linguistics.jmdict_bootstrap import (
    CONVERTER_VERSION,
    JmdictIntegrityError,
    JmdictManifest,
    JmdictNormalizedArtifact,
    JmdictSourceArtifact,
    convert_simplified_jmdict,
)
from mangasensei.linguistics.jmdict_packs import (
    JmdictPackDescriptor,
    JmdictPackRegistry,
    download_jmdict_pack,
    load_jmdict_packs,
    resolve_jmdict_pack,
    verify_jmdict_pack,
)


def test_reviewed_registry_contains_only_english() -> None:
    packs = load_jmdict_packs()

    assert set(packs) == {"en"}
    english = packs["en"]
    assert english.upstream_language == "eng"
    assert english.manifest.normalized.filename == "jmdict.json"


def test_reviewed_english_metadata_matches_pinned_source() -> None:
    english = load_jmdict_packs()["en"].manifest

    assert english.source.filename == "jmdict-eng-3.6.2+20260803141815.json.zip"
    assert english.source.sha256 == (
        "1806d2817215ebe7ded997c8dac4831a3335d83ed12f321ac869a97e745d3a5c"
    )
    assert english.source.size_bytes == 11_475_140
    assert english.normalized.sha256 == (
        "93026b2540d40e9175a11d9b770e77b21ef6be5daf136cee680fa550c62193dc"
    )
    assert english.normalized.size_bytes == 65_872_497
    assert english.normalized.entry_count == 218_290


@pytest.mark.parametrize("language", ["de", "pt-BR"])
def test_non_english_product_languages_fail_closed(language: str) -> None:
    with pytest.raises(JmdictIntegrityError, match="unsupported dictionary language"):
        resolve_jmdict_pack(language)


def test_pack_target_path_preserves_configured_english_location(tmp_path: Path) -> None:
    configured = tmp_path / "custom" / "english.json"

    assert resolve_jmdict_pack("en").target_path(configured) == configured


@pytest.mark.asyncio
async def test_english_pack_bootstrap_uses_safe_v3_conversion(tmp_path: Path) -> None:
    registry_path, source_url, source = write_fixture_registry(tmp_path)

    async def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == source_url
        return httpx.Response(200, content=source, request=request)

    configured = tmp_path / "data" / "jmdict.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await download_jmdict_pack(
            configured,
            language="en",
            registry_path=registry_path,
            client=client,
        )

    forms = normalized_form_map(configured)
    assert forms[("半片", "はんぺん")] == ["fish cake", "half ticket"]
    assert forms[("半平", "はんぺい")] == ["fish cake"]
    assert ("半片", "はんぺい") not in forms
    assert verify_jmdict_pack(configured, registry_path=registry_path) == configured


@pytest.mark.asyncio
async def test_pack_download_rejects_source_checksum_mismatch(tmp_path: Path) -> None:
    registry_path, source_url, source = write_fixture_registry(tmp_path)
    tampered = bytearray(source)
    tampered[-1] ^= 1

    async def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == source_url
        return httpx.Response(200, content=bytes(tampered), request=request)

    target = tmp_path / "data" / "jmdict.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(JmdictIntegrityError, match="checksum mismatch"):
            await download_jmdict_pack(
                target,
                registry_path=registry_path,
                client=client,
            )

    assert not target.exists()


def test_third_party_notice_tracks_english_manifest_provenance() -> None:
    root = Path(__file__).parents[2]
    notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    manifest = load_jmdict_packs()["en"].manifest

    expected_values = (
        manifest.source.filename,
        manifest.source.sha256,
        manifest.source.source_version,
        manifest.normalized.filename,
        manifest.normalized.sha256,
        str(manifest.normalized.size_bytes),
        str(manifest.normalized.entry_count),
    )
    for value in expected_values:
        assert f"`{value}`" in notice


def normalized_form_map(path: Path) -> dict[tuple[str, str], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload["entries"]
    assert len(entries) == 1
    return {
        (form["lemma"], form["reading"]): form["meanings"]
        for form in entries[0]["forms"]
    }


def write_fixture_registry(tmp_path: Path) -> tuple[Path, str, bytes]:
    version = "jmdict-simplified-3.6.2+fixture"
    payload = restricted_payload()
    source = zipped_source(payload, filename="jmdict-eng.json")
    source_url = "https://example.test/jmdict-eng.json.zip"
    normalized = convert_simplified_jmdict(
        payload,
        version=version,
        language="eng",
        source_url=source_url,
        license_id="CC-BY-SA-4.0",
        attribution="JMdict data provided by EDRDG",
    )
    manifest = JmdictManifest(
        version="fixture",
        source=JmdictSourceArtifact(
            filename="jmdict-eng.json.zip",
            url=source_url,
            sha256=hashlib.sha256(source).hexdigest(),
            size_bytes=len(source),
            max_uncompressed_bytes=1_000_000,
            language="eng",
            source_version=version,
            license_id="CC-BY-SA-4.0",
            attribution="JMdict data provided by EDRDG",
            redistribution_status="local-bootstrap-derived-data",
        ),
        normalized=JmdictNormalizedArtifact(
            filename="jmdict.json",
            sha256=hashlib.sha256(normalized).hexdigest(),
            size_bytes=len(normalized),
            entry_count=1,
            converter_version=CONVERTER_VERSION,
        ),
    )
    (tmp_path / "jmdict_manifest.json").write_text(
        manifest.model_dump_json(), encoding="utf-8"
    )
    registry = JmdictPackRegistry(
        version="fixture",
        source_snapshot=version,
        default_language="en",
        fallback_language="en",
        packs=(
            JmdictPackDescriptor(
                product_language="en",
                upstream_language="eng",
                manifest="jmdict_manifest.json",
            ),
        ),
    )
    registry_path = tmp_path / "jmdict_packs.json"
    registry_path.write_text(registry.model_dump_json(), encoding="utf-8")
    return registry_path, source_url, source


def restricted_payload() -> dict[str, object]:
    def gloss(text: str) -> dict[str, object]:
        return {"lang": "eng", "text": text, "gender": None, "type": None}

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
                    {
                        "gloss": [gloss("fish cake")],
                        "appliesToKanji": ["*"],
                        "appliesToKana": ["*"],
                    },
                    {
                        "gloss": [gloss("half ticket")],
                        "appliesToKanji": ["半片"],
                        "appliesToKana": ["はんぺん"],
                    },
                ],
            }
        ]
    }


def zipped_source(payload: dict[str, object], *, filename: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            filename,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    return buffer.getvalue()
