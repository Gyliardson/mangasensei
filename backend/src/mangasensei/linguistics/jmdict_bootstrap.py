"""Verified bootstrap for the local normalized JMdict JSON artifact."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile

import httpx
from pydantic import BaseModel, ConfigDict, Field

from mangasensei.linguistics.jmdict import (
    NORMALIZED_CONVERTER_VERSION,
    JsonJmdictDictionary,
)

CONVERTER_VERSION = NORMALIZED_CONVERTER_VERSION


class JmdictIntegrityError(RuntimeError):
    """The reviewed JMdict source or normalized local output is invalid."""


class JmdictSourceArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str = Field(pattern=r"^[A-Za-z0-9._+-]+\.zip$")
    url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    max_uncompressed_bytes: int = Field(gt=0)
    language: str = Field(pattern=r"^[a-z]{3}$")
    source_version: str = Field(min_length=1, max_length=57)
    license_id: str = Field(min_length=1, max_length=64)
    attribution: str = Field(min_length=1, max_length=512)
    redistribution_status: str = Field(min_length=1, max_length=128)


class JmdictNormalizedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str = Field(pattern=r"^[A-Za-z0-9._-]+\.json$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    entry_count: int = Field(gt=0)
    converter_version: str = Field(min_length=1, max_length=64)


class JmdictManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str = Field(min_length=1, max_length=64)
    source: JmdictSourceArtifact
    normalized: JmdictNormalizedArtifact

    @classmethod
    def load(cls, path: Path) -> JmdictManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class _KanaForm:
    text: str
    applies_to_kanji: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Sense:
    meanings: tuple[str, ...]
    applies_to_kanji: tuple[str, ...]
    applies_to_kana: tuple[str, ...]


def default_manifest_path() -> Path:
    return Path(__file__).with_name("jmdict_manifest.json")


async def build_normalized_jmdict(
    manifest: JmdictManifest, client: httpx.AsyncClient
) -> bytes:
    """Build deterministic normalized bytes from the manifest-pinned source."""
    source_zip = await _download_source(manifest.source, client)
    source_payload = _extract_source_payload(source_zip, manifest.source)
    return convert_simplified_jmdict(
        source_payload,
        version=manifest.source.source_version,
        language=manifest.source.language,
        source_url=manifest.source.url,
        license_id=manifest.source.license_id,
        attribution=manifest.source.attribution,
    )


async def download_jmdict(
    target: Path,
    *,
    manifest_path: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    manifest = JmdictManifest.load(manifest_path or default_manifest_path())
    _require_current_converter(manifest)
    if client is None:
        timeout = httpx.Timeout(300.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as owned_client:
            return await download_jmdict(
                target, manifest_path=manifest_path, client=owned_client
            )

    if _is_verified(target, manifest):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f"{target.name}.lock")
    owns_lock = await _acquire_lock(lock_path, target, manifest)
    if not owns_lock:
        return False

    part_path = target.with_name(f"{target.name}.part-{os.getpid()}-{secrets.token_hex(4)}")
    try:
        if _is_verified(target, manifest):
            return False
        normalized = await build_normalized_jmdict(manifest, client)
        _verify_normalized_bytes(normalized, manifest)
        with part_path.open("xb") as output:
            output.write(normalized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(part_path, target)
        verify_jmdict(target, manifest=manifest)
        return True
    finally:
        part_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def verify_jmdict(
    target: Path,
    *,
    manifest: JmdictManifest | None = None,
    manifest_path: Path | None = None,
) -> Path:
    resolved_manifest = manifest or JmdictManifest.load(manifest_path or default_manifest_path())
    _require_current_converter(resolved_manifest)
    if not target.is_file() or target.stat().st_size != resolved_manifest.normalized.size_bytes:
        raise JmdictIntegrityError(
            f"dictionary size mismatch: {resolved_manifest.normalized.filename}"
        )
    digest = hashlib.sha256()
    with target.open("rb") as dictionary_file:
        while chunk := dictionary_file.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != resolved_manifest.normalized.sha256:
        raise JmdictIntegrityError(
            f"dictionary checksum mismatch: {resolved_manifest.normalized.filename}"
        )
    dictionary = JsonJmdictDictionary(target)
    if dictionary.entry_count != resolved_manifest.normalized.entry_count:
        raise JmdictIntegrityError(
            f"dictionary entry count mismatch: {resolved_manifest.normalized.filename}"
        )
    if dictionary.version != resolved_manifest.source.source_version:
        raise JmdictIntegrityError(
            f"dictionary version mismatch: {resolved_manifest.normalized.filename}"
        )
    return target


def convert_simplified_jmdict(
    payload: dict[str, Any],
    *,
    version: str,
    language: str,
    source_url: str,
    license_id: str,
    attribution: str,
) -> bytes:
    words = payload.get("words")
    if not isinstance(words, list):
        raise JmdictIntegrityError("jmdict-simplified payload must contain a words array")
    entries = [entry for word in words if (entry := _convert_word(word, language))]
    entries.sort(key=lambda entry: entry["id"])
    normalized = {
        "converterVersion": CONVERTER_VERSION,
        "version": version,
        "source": {
            "url": source_url,
            "license": license_id,
            "attribution": attribution,
        },
        "entries": entries,
    }
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


async def _download_source(source: JmdictSourceArtifact, client: httpx.AsyncClient) -> bytes:
    digest = hashlib.sha256()
    size = 0
    output = bytearray()
    try:
        async with client.stream(
            "GET", source.url, follow_redirects=True, timeout=300.0
        ) as response:
            response.raise_for_status()
            if response.url.scheme != "https":
                raise JmdictIntegrityError("dictionary download redirected outside HTTPS")
            async for chunk in response.aiter_bytes(1024 * 1024):
                size += len(chunk)
                if size > source.size_bytes:
                    raise JmdictIntegrityError(f"dictionary size mismatch: {source.filename}")
                digest.update(chunk)
                output.extend(chunk)
    except httpx.HTTPError as exc:
        raise JmdictIntegrityError(f"dictionary download failed: {source.filename}") from exc
    if size != source.size_bytes:
        raise JmdictIntegrityError(f"dictionary size mismatch: {source.filename}")
    if digest.hexdigest() != source.sha256:
        raise JmdictIntegrityError(f"dictionary checksum mismatch: {source.filename}")
    return bytes(output)


def _extract_source_payload(source_zip: bytes, source: JmdictSourceArtifact) -> dict[str, Any]:
    try:
        with ZipFile(BytesIO(source_zip)) as archive:
            files = [info for info in archive.infolist() if not info.is_dir()]
            if len(files) != 1:
                raise JmdictIntegrityError("dictionary archive must contain one JSON file")
            info = files[0]
            if "/" in info.filename or "\\" in info.filename or not info.filename.endswith(".json"):
                raise JmdictIntegrityError("dictionary archive contains an unsafe filename")
            if info.file_size > source.max_uncompressed_bytes:
                raise JmdictIntegrityError("dictionary archive exceeds uncompressed size limit")
            with archive.open(info) as source_file:
                content = source_file.read(source.max_uncompressed_bytes + 1)
    except BadZipFile as exc:
        raise JmdictIntegrityError("dictionary archive is not a valid ZIP file") from exc
    if len(content) > source.max_uncompressed_bytes:
        raise JmdictIntegrityError("dictionary archive exceeds uncompressed size limit")
    try:
        decoded = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JmdictIntegrityError("dictionary JSON is invalid") from exc
    if not isinstance(decoded, dict):
        raise JmdictIntegrityError("dictionary JSON root must be an object")
    return decoded


def _convert_word(word: Any, language: str) -> dict[str, Any] | None:
    if not isinstance(word, dict):
        raise JmdictIntegrityError("jmdict-simplified word must be an object")
    raw_id = str(word.get("id", "")).strip()
    if not raw_id:
        raise JmdictIntegrityError("jmdict-simplified word is missing id")
    kanji = tuple(_text_items(word.get("kanji"), field="kanji"))
    kana = _kana_items(word.get("kana"), kanji)
    kana_texts = tuple(item.text for item in kana)
    senses = _sense_items(word.get("sense"), kanji, kana_texts, language)
    forms: dict[tuple[str, str], tuple[str, ...]] = {}
    for kana_form in kana:
        self_meanings = _meanings_for_form(senses, kanji=None, kana=kana_form.text)
        if self_meanings:
            _add_form(forms, kana_form.text, kana_form.text, self_meanings)
        for spelling in kanji:
            if not _restriction_applies(kana_form.applies_to_kanji, spelling):
                continue
            meanings = _meanings_for_form(senses, kanji=spelling, kana=kana_form.text)
            if meanings:
                _add_form(forms, spelling, kana_form.text, meanings)
    if not forms:
        return None
    return {
        "id": f"jmdict-{raw_id}",
        "forms": [
            {"lemma": lemma, "reading": reading, "meanings": list(meanings)}
            for (lemma, reading), meanings in sorted(forms.items())
        ],
    }


def _text_items(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise JmdictIntegrityError(f"jmdict-simplified {field} must be an array")
    texts: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            raise JmdictIntegrityError(f"jmdict-simplified {field} item must be an object")
        text = str(item.get("text", "")).strip()
        if text and text not in texts:
            texts.append(text)
    return texts


def _kana_items(value: Any, kanji: tuple[str, ...]) -> tuple[_KanaForm, ...]:
    if not isinstance(value, list):
        raise JmdictIntegrityError("jmdict-simplified kana must be an array")
    result: list[_KanaForm] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise JmdictIntegrityError("jmdict-simplified kana item must be an object")
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        if text in seen:
            raise JmdictIntegrityError("jmdict-simplified kana contains a duplicate text")
        seen.add(text)
        restrictions = _restriction_items(
            item.get("appliesToKanji"),
            field="kana appliesToKanji",
            valid_values=set(kanji),
            allow_empty=True,
        )
        result.append(_KanaForm(text=text, applies_to_kanji=restrictions))
    return tuple(result)


def _sense_items(
    value: Any,
    kanji: tuple[str, ...],
    kana: tuple[str, ...],
    language: str,
) -> tuple[_Sense, ...]:
    if not isinstance(value, list):
        raise JmdictIntegrityError("jmdict-simplified sense must be an array")
    result: list[_Sense] = []
    for sense in value:
        if not isinstance(sense, dict):
            raise JmdictIntegrityError("jmdict-simplified sense item must be an object")
        applies_to_kanji = _restriction_items(
            sense.get("appliesToKanji"),
            field="sense appliesToKanji",
            valid_values=set(kanji),
            allow_empty=False,
        )
        applies_to_kana = _restriction_items(
            sense.get("appliesToKana"),
            field="sense appliesToKana",
            valid_values=set(kana),
            allow_empty=False,
        )
        glosses = sense.get("gloss")
        if not isinstance(glosses, list):
            raise JmdictIntegrityError("jmdict-simplified gloss must be an array")
        meanings: list[str] = []
        for gloss in glosses:
            if not isinstance(gloss, dict):
                raise JmdictIntegrityError("jmdict-simplified gloss item must be an object")
            text = str(gloss.get("text", "")).strip()
            if gloss.get("lang") == language and text and text not in meanings:
                meanings.append(text)
        result.append(
            _Sense(
                meanings=tuple(meanings),
                applies_to_kanji=applies_to_kanji,
                applies_to_kana=applies_to_kana,
            )
        )
    return tuple(result)


def _restriction_items(
    value: Any,
    *,
    field: str,
    valid_values: set[str],
    allow_empty: bool,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise JmdictIntegrityError(f"jmdict-simplified {field} must be an array")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise JmdictIntegrityError(f"jmdict-simplified {field} contains an invalid value")
        if item not in items:
            items.append(item)
    if not items and not allow_empty:
        raise JmdictIntegrityError(f"jmdict-simplified {field} must not be empty")
    if "*" in items and len(items) != 1:
        raise JmdictIntegrityError(f"jmdict-simplified {field} mixes wildcard and explicit values")
    unknown = set(items) - {"*"} - valid_values
    if unknown:
        raise JmdictIntegrityError(f"jmdict-simplified {field} references an unknown form")
    return tuple(items)


def _meanings_for_form(
    senses: tuple[_Sense, ...], *, kanji: str | None, kana: str
) -> tuple[str, ...]:
    meanings: list[str] = []
    for sense in senses:
        if not _restriction_applies(sense.applies_to_kana, kana):
            continue
        if kanji is None:
            if sense.applies_to_kanji != ("*",):
                continue
        elif not _restriction_applies(sense.applies_to_kanji, kanji):
            continue
        for meaning in sense.meanings:
            if meaning not in meanings:
                meanings.append(meaning)
    return tuple(meanings)


def _restriction_applies(restrictions: tuple[str, ...], value: str) -> bool:
    return "*" in restrictions or value in restrictions


def _add_form(
    forms: dict[tuple[str, str], tuple[str, ...]],
    lemma: str,
    reading: str,
    meanings: tuple[str, ...],
) -> None:
    key = (lemma, reading)
    existing = forms.get(key, ())
    forms[key] = tuple(dict.fromkeys((*existing, *meanings)))


def _verify_normalized_bytes(content: bytes, manifest: JmdictManifest) -> None:
    _require_current_converter(manifest)
    if len(content) != manifest.normalized.size_bytes:
        raise JmdictIntegrityError(
            f"dictionary size mismatch: {manifest.normalized.filename}"
        )
    if hashlib.sha256(content).hexdigest() != manifest.normalized.sha256:
        raise JmdictIntegrityError(
            f"dictionary checksum mismatch: {manifest.normalized.filename}"
        )
    payload = json.loads(content.decode("utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("converterVersion") != CONVERTER_VERSION
        or not isinstance(payload.get("entries"), list)
    ):
        raise JmdictIntegrityError("dictionary normalized JSON is invalid")
    if len(payload["entries"]) != manifest.normalized.entry_count:
        raise JmdictIntegrityError(
            f"dictionary entry count mismatch: {manifest.normalized.filename}"
        )


def _require_current_converter(manifest: JmdictManifest) -> None:
    if manifest.normalized.converter_version != CONVERTER_VERSION:
        raise JmdictIntegrityError(
            f"dictionary converter mismatch: expected {CONVERTER_VERSION}"
        )


def _is_verified(target: Path, manifest: JmdictManifest) -> bool:
    try:
        verify_jmdict(target, manifest=manifest)
    except (JmdictIntegrityError, OSError, ValueError):
        return False
    return True


async def _acquire_lock(
    lock_path: Path,
    target: Path,
    manifest: JmdictManifest,
    *,
    wait_seconds: float = 600.0,
) -> bool:
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _is_verified(target, manifest):
                return False
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for dictionary lock: {target.name}"
                ) from None
            try:
                if time.time() - os.stat(lock_path).st_mtime > 3_600:
                    try:
                        os.unlink(lock_path)
                    except FileNotFoundError:
                        continue
                    continue
            except FileNotFoundError:
                continue
            await asyncio.sleep(0.25)
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as lock_file:
                lock_file.write(str(os.getpid()))
            return True
