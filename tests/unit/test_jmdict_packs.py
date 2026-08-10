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
)
from mangasensei.linguistics.jmdict_packs import (
    JmdictPackDescriptor,
    JmdictPackRegistry,
    download_jmdict_pack,
    load_jmdict_packs,
    resolve_jmdict_pack,
)


def test_reviewed_registry_maps_product_and_upstream_languages_explicitly() -> None:
    packs = load_jmdict_packs()

    assert set(packs) == {"de", "en"}
    assert packs["en"].upstream_language == "eng"
    assert packs["de"].upstream_language == "ger"
    assert packs["en"].manifest.source.source_version == packs["de"].manifest.source.source_version
    assert packs["en"].manifest.normalized.filename == "jmdict.json"
    assert packs["de"].manifest.normalized.filename == "jmdict-de.json"


def test_reviewed_pack_metadata_matches_pinned_sources() -> None:
    packs = load_jmdict_packs()
    english = packs["en"].manifest
    german = packs["de"].manifest

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

    assert german.source.filename == "jmdict-ger-3.6.2+20260803141815.json.zip"
    assert german.source.sha256 == (
        "4da33c567bb03490ffc9819fd1b3e8efc6522a4a790c99b0d2677094f184b7b3"
    )
    assert german.source.size_bytes == 7_014_092
    assert german.normalized.entry_count == 128_931


def test_unsupported_product_language_fails_closed() -> None:
    with pytest.raises(JmdictIntegrityError, match="unsupported dictionary language"):
        resolve_jmdict_pack("pt-BR")


def test_pack_target_path_preserves_configured_english_location(tmp_path: Path) -> None:
    configured = tmp_path / "custom" / "english.json"

    assert resolve_jmdict_pack("en").target_path(configured) == configured
    assert resolve_jmdict_pack("de").target_path(configured) == configured.parent / "jmdict-de.json"


def test_registry_rejects_snapshot_mismatch(tmp_path: Path) -> None:
    registry_path, _ = write_fixture_registry(tmp_path)
    german_path = tmp_path / "jmdict_manifest.de.json"
    german = json.loads(german_path.read_text(encoding="utf-8"))
    german["source"]["source_version"] = "jmdict-simplified-other-snapshot"
    german_path.write_text(json.dumps(german), encoding="utf-8")

    with pytest.raises(JmdictIntegrityError, match="source snapshot mismatch"):
        load_jmdict_packs(registry_path)


@pytest.mark.asyncio
async def test_english_and_german_bootstrap_use_same_safe_v3_conversion(
    tmp_path: Path,
) -> None:
    registry_path, source_by_url = write_fixture_registry(tmp_path)

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=source_by_url[str(request.url)], request=request)

    configured = tmp_path / "data" / "jmdict.json"
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        assert await download_jmdict_pack(
            configured, language="en", registry_path=registry_path, client=client
        )
        assert await download_jmdict_pack(
            configured, language="de", registry_path=registry_path, client=client
        )

    english = JsonJmdictDictionary(configured)
    german = JsonJmdictDictionary(configured.parent / "jmdict-de.json")
    english_hanpen = english.lookup("半片", "ハンペン")
    german_hanpen = german.lookup("半片", "ハンペン")
    english_hanpei = english.lookup("半平", "ハンペイ")
    german_hanpei = german.lookup("半平", "ハンペイ")

    assert english_hanpen is not None
    assert german_hanpen is not None
    assert english_hanpei is not None
    assert german_hanpei is not None
    assert english_hanpen.meanings == ("fish cake", "half ticket")
    assert german_hanpen.meanings == ("Fischkuchen", "halbe Karte")
    assert english_hanpei.meanings == ("fish cake",)
    assert german_hanpei.meanings == ("Fischkuchen",)
    assert english.lookup("半片", "ハンペイ") is None
    assert german.lookup("半片", "ハンペイ") is None


def test_third_party_notice_tracks_manifest_backed_provenance() -> None:
    root = Path(__file__).parents[2]
    notice = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

    for pack in load_jmdict_packs().values():
        manifest = pack.manifest
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


def write_fixture_registry(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    version = "jmdict-simplified-3.6.2+fixture"
    source_by_url: dict[str, bytes] = {}
    descriptors: list[JmdictPackDescriptor] = []
    language_data = {
        "en": ("eng", "fish cake", "half ticket", "jmdict.json"),
        "de": ("ger", "Fischkuchen", "halbe Karte", "jmdict-de.json"),
    }
    for product_language, (upstream_language, general, restricted, normalized_filename) in (
        language_data.items()
    ):
        payload = restricted_payload(upstream_language, general, restricted)
        source = zipped_source(payload, filename=f"jmdict-{upstream_language}.json")
        url = f"https://example.test/jmdict-{upstream_language}.json.zip"
        normalized = convert_simplified_jmdict(
            payload,
            version=version,
            language=upstream_language,
            source_url=url,
            license_id="CC-BY-SA-4.0",
            attribution="JMdict data provided by EDRDG",
        )
        manifest = JmdictManifest(
            version="fixture",
            source=JmdictSourceArtifact(
                filename=f"jmdict-{upstream_language}.json.zip",
                url=url,
                sha256=hashlib.sha256(source).hexdigest(),
                size_bytes=len(source),
                max_uncompressed_bytes=1_000_000,
                language=upstream_language,
                source_version=version,
                license_id="CC-BY-SA-4.0",
                attribution="JMdict data provided by EDRDG",
                redistribution_status="local-bootstrap-derived-data",
            ),
            normalized=JmdictNormalizedArtifact(
                filename=normalized_filename,
                sha256=hashlib.sha256(normalized).hexdigest(),
                size_bytes=len(normalized),
                entry_count=1,
                converter_version=CONVERTER_VERSION,
            ),
        )
        manifest_filename = (
            "jmdict_manifest.json" if product_language == "en" else "jmdict_manifest.de.json"
        )
        (tmp_path / manifest_filename).write_text(manifest.model_dump_json(), encoding="utf-8")
        descriptors.append(
            JmdictPackDescriptor(
                product_language=product_language,
                upstream_language=upstream_language,
                manifest=manifest_filename,
            )
        )
        source_by_url[url] = source

    registry = JmdictPackRegistry(
        version="fixture",
        source_snapshot=version,
        default_language="en",
        fallback_language="en",
        packs=tuple(descriptors),
    )
    registry_path = tmp_path / "jmdict_packs.json"
    registry_path.write_text(registry.model_dump_json(), encoding="utf-8")
    return registry_path, source_by_url


def restricted_payload(
    language: str,
    general_meaning: str,
    restricted_meaning: str,
) -> dict[str, object]:
    def gloss(text: str) -> dict[str, object]:
        return {"lang": language, "text": text, "gender": None, "type": None}

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
                        "gloss": [gloss(general_meaning)],
                        "appliesToKanji": ["*"],
                        "appliesToKana": ["*"],
                    },
                    {
                        "gloss": [gloss(restricted_meaning)],
                        "appliesToKanji": ["半片"],
                        "appliesToKana": ["*"],
                    },
                ],
            }
        ]
    }


def zipped_source(payload: dict[str, object], *, filename: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            filename,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    return buffer.getvalue()
