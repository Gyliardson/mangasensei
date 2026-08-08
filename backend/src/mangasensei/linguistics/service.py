"""Ports and orchestration for deterministic linguistic analysis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    id: str
    meanings: tuple[str, ...]
    source: str
    jlpt_level: str | None
    jlpt_official: bool


@dataclass(frozen=True, slots=True)
class LinguisticToken:
    id: str
    surface: str
    lemma: str
    reading: str
    part_of_speech: str
    dictionary_id: str | None
    meanings: tuple[str, ...]
    source: str | None
    jlpt_level: str | None
    jlpt_official: bool | None


class Tokenizer(Protocol):
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]: ...


class Dictionary(Protocol):
    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None: ...


class LinguisticService:
    def __init__(self, tokenizer: Tokenizer, dictionary: Dictionary) -> None:
        self._tokenizer = tokenizer
        self._dictionary = dictionary

    @property
    def dictionary_version(self) -> str:
        return str(getattr(self._dictionary, "version", "configured-local-data"))

    @property
    def dictionary_digest(self) -> bytes:
        digest = getattr(self._dictionary, "digest", None)
        if isinstance(digest, bytes) and len(digest) == 32:
            return digest
        return hashlib.sha256(self.dictionary_version.encode()).digest()

    def analyze(self, region_id: str, text: str) -> tuple[LinguisticToken, ...]:
        analyzed: list[LinguisticToken] = []
        for index, (surface, lemma, reading, part_of_speech) in enumerate(
            self._tokenizer.tokenize(text)
        ):
            entry = self._dictionary.lookup(lemma, reading)
            analyzed.append(
                LinguisticToken(
                    id=f"{region_id}:token:{index}",
                    surface=surface,
                    lemma=lemma,
                    reading=reading,
                    part_of_speech=part_of_speech,
                    dictionary_id=entry.id if entry else None,
                    meanings=entry.meanings if entry else (),
                    source=entry.source if entry else None,
                    jlpt_level=entry.jlpt_level if entry else None,
                    jlpt_official=entry.jlpt_official if entry else None,
                )
            )
        return tuple(analyzed)
