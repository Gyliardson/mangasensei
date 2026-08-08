"""Read-only normalized JMdict index with explicitly unofficial JLPT metadata."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from mangasensei.linguistics.service import DictionaryEntry

NORMALIZED_CONVERTER_VERSION = "mangasensei-jmdict-v2"


class DictionaryDataError(ValueError):
    """The local normalized dictionary does not satisfy its data contract."""


class JsonJmdictDictionary:
    def __init__(self, path: Path) -> None:
        content = path.read_bytes()
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise DictionaryDataError("JMdict payload must contain an entries array")
        if payload.get("converterVersion") != NORMALIZED_CONVERTER_VERSION:
            raise DictionaryDataError(
                f"JMdict payload must use {NORMALIZED_CONVERTER_VERSION}"
            )
        version = str(payload.get("version", "unknown"))
        mutable_index: dict[tuple[str, str], list[DictionaryEntry]] = defaultdict(list)
        for raw_entry in payload["entries"]:
            for key, entry in _normalize_entry(raw_entry, version):
                mutable_index[key].append(entry)
        self._index = {key: tuple(entries) for key, entries in mutable_index.items()}
        self.version = version
        self.digest = hashlib.sha256(content).digest()
        self.entry_count = len(payload["entries"])

    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        matches = self._index.get((lemma, _hiragana(reading)), ())
        return matches[0] if len(matches) == 1 else None


def _normalize_entry(raw: Any, version: str) -> tuple[tuple[tuple[str, str], DictionaryEntry], ...]:
    if not isinstance(raw, dict):
        raise DictionaryDataError("JMdict entry must be an object")
    entry_id = str(raw.get("id", "")).strip()
    raw_forms = raw.get("forms")
    if not entry_id or not isinstance(raw_forms, list) or not raw_forms:
        raise DictionaryDataError("JMdict entry is missing id or forms")
    jlpt = raw.get("jlptLevel")
    jlpt_level = str(jlpt) if jlpt in {"N1", "N2", "N3", "N4", "N5"} else None
    normalized: list[tuple[tuple[str, str], DictionaryEntry]] = []
    seen_keys: set[tuple[str, str]] = set()
    for raw_form in raw_forms:
        if not isinstance(raw_form, dict):
            raise DictionaryDataError("JMdict form must be an object")
        raw_lemma = str(raw_form.get("lemma", "")).strip()
        raw_reading = str(raw_form.get("reading", "")).strip()
        raw_meanings = raw_form.get("meanings")
        if (
            not raw_lemma
            or not raw_reading
            or not isinstance(raw_meanings, list)
            or not raw_meanings
        ):
            raise DictionaryDataError("JMdict form is missing lemma, reading or meanings")
        meanings = tuple(
            dict.fromkeys(str(item).strip() for item in raw_meanings if str(item).strip())
        )
        if not meanings:
            raise DictionaryDataError("JMdict form has no meanings")
        reading = _hiragana(raw_reading)
        lemma = _hiragana(raw_lemma) if raw_lemma == raw_reading else raw_lemma
        key = (lemma, reading)
        if key in seen_keys:
            raise DictionaryDataError("JMdict entry contains a duplicate form")
        seen_keys.add(key)
        normalized.append(
            (
                key,
                DictionaryEntry(
                    id=entry_id,
                    meanings=meanings,
                    source=f"JMdict {version}",
                    jlpt_level=jlpt_level,
                    jlpt_official=False,
                ),
            )
        )
    return tuple(normalized)


def _hiragana(value: str) -> str:
    return "".join(
        chr(ord(character) - 0x60) if "ァ" <= character <= "ヶ" else character
        for character in value
    )
