"""Read-only normalized JMdict index with explicitly unofficial JLPT metadata."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from mangasensei.linguistics.service import (
    DictionaryEntry,
    DictionaryLookupResult,
    LexicalFormIdentity,
)

NORMALIZED_CONVERTER_VERSION = "mangasensei-jmdict-v3"
DICTIONARY_NAMESPACE = "JMdict"


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
        identity_index: dict[LexicalFormIdentity, DictionaryEntry] = {}
        entry_ids: set[str] = set()
        for raw_entry in payload["entries"]:
            normalized_forms = _normalize_entry(raw_entry, version)
            for key, entry in normalized_forms:
                mutable_index[key].append(entry)
                if entry.identity in identity_index:
                    raise DictionaryDataError("JMdict contains a duplicate lexical identity")
                identity_index[entry.identity] = entry
                entry_ids.add(entry.identity.entry_id)
        self._index = {
            key: tuple(sorted(entries, key=lambda entry: entry.identity))
            for key, entries in mutable_index.items()
        }
        self._identity_index = identity_index
        self._entry_ids = frozenset(entry_ids)
        self.version = version
        self.digest = hashlib.sha256(content).digest()
        self.entry_count = len(payload["entries"])

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        matches = self._index.get((lemma, _hiragana(reading)), ())
        return DictionaryLookupResult.from_candidates(matches)

    def lookup_identity(self, identity: LexicalFormIdentity) -> DictionaryEntry | None:
        """Return one already-resolved canonical form without rerunning candidate selection."""
        if identity.dictionary_namespace != DICTIONARY_NAMESPACE:
            return None
        return self._identity_index.get(identity)

    def contains_entry(self, entry_id: str) -> bool:
        """Return whether this language pack contains any form for a canonical JMdict entry."""
        return entry_id in self._entry_ids


def _normalize_entry(
    raw: Any, version: str
) -> tuple[tuple[tuple[str, str], DictionaryEntry], ...]:
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
        key = _normalized_form_key(raw_lemma, raw_reading)
        if key in seen_keys:
            raise DictionaryDataError("JMdict entry contains a duplicate form")
        seen_keys.add(key)
        normalized.append(
            (
                key,
                DictionaryEntry(
                    identity=LexicalFormIdentity(
                        dictionary_namespace=DICTIONARY_NAMESPACE,
                        entry_id=entry_id,
                        lemma=key[0],
                        reading=key[1],
                    ),
                    meanings=meanings,
                    source=f"JMdict {version}",
                    jlpt_level=jlpt_level,
                    jlpt_official=False,
                ),
            )
        )
    return tuple(normalized)


def _normalized_form_key(lemma: str, reading: str) -> tuple[str, str]:
    """Return the canonical runtime key used by both converter and dictionary loader."""
    normalized_reading = _hiragana(reading)
    normalized_lemma = _hiragana(lemma) if lemma == reading else lemma
    return normalized_lemma, normalized_reading


def _hiragana(value: str) -> str:
    return "".join(
        chr(ord(character) - 0x60) if "ァ" <= character <= "ヶ" else character
        for character in value
    )
