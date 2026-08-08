"""Read-only normalized JMdict index with explicitly unofficial JLPT metadata."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from mangasensei.linguistics.service import DictionaryEntry


class DictionaryDataError(ValueError):
    """The local normalized dictionary does not satisfy its data contract."""


class JsonJmdictDictionary:
    def __init__(self, path: Path) -> None:
        content = path.read_bytes()
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
            raise DictionaryDataError("JMdict payload must contain an entries array")
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
    kanji = tuple(str(item) for item in raw.get("kanji", ()) if str(item))
    readings = tuple(_hiragana(str(item)) for item in raw.get("readings", ()) if str(item))
    meanings = tuple(str(item) for item in raw.get("meanings", ()) if str(item))
    if not entry_id or not readings or not meanings:
        raise DictionaryDataError("JMdict entry is missing id, reading or meaning")
    jlpt = raw.get("jlptLevel")
    jlpt_level = str(jlpt) if jlpt in {"N1", "N2", "N3", "N4", "N5"} else None
    entry = DictionaryEntry(
        id=entry_id,
        meanings=meanings,
        source=f"JMdict {version}",
        jlpt_level=jlpt_level,
        jlpt_official=False,
    )
    lemmas = tuple(dict.fromkeys((*kanji, *readings)))
    return tuple(((lemma, reading), entry) for lemma in lemmas for reading in readings)


def _hiragana(value: str) -> str:
    return "".join(
        chr(ord(character) - 0x60) if "ァ" <= character <= "ヶ" else character
        for character in value
    )
