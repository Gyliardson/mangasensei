"""Ports and orchestration for deterministic linguistic and lexical analysis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True, order=True)
class LexicalFormIdentity:
    """Language-neutral identity for one applicable normalized dictionary form."""

    dictionary_namespace: str
    entry_id: str
    lemma: str
    reading: str

    @property
    def form_key(self) -> tuple[str, str]:
        return self.lemma, self.reading

    @property
    def transport_id(self) -> str:
        payload = "\0".join(
            (self.dictionary_namespace, self.entry_id, self.lemma, self.reading)
        ).encode("utf-8")
        return f"lexical-{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    identity: LexicalFormIdentity
    meanings: tuple[str, ...]
    source: str
    jlpt_level: str | None
    jlpt_official: bool

    @property
    def id(self) -> str:
        return self.identity.entry_id


class DictionaryLookupStatus(StrEnum):
    NOT_FOUND = "not_found"
    UNIQUE = "unique"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class DictionaryLookupResult:
    status: DictionaryLookupStatus
    candidates: tuple[DictionaryEntry, ...]

    @classmethod
    def from_candidates(cls, candidates: tuple[DictionaryEntry, ...]) -> DictionaryLookupResult:
        ordered = tuple(sorted(candidates, key=lambda candidate: candidate.identity))
        if not ordered:
            status = DictionaryLookupStatus.NOT_FOUND
        elif len(ordered) == 1:
            status = DictionaryLookupStatus.UNIQUE
        else:
            status = DictionaryLookupStatus.AMBIGUOUS
        return cls(status=status, candidates=ordered)

    @property
    def unique_entry(self) -> DictionaryEntry | None:
        return self.candidates[0] if self.status is DictionaryLookupStatus.UNIQUE else None


@dataclass(frozen=True, slots=True)
class LinguisticToken:
    id: str
    surface: str
    lemma: str
    reading: str
    part_of_speech: str


@dataclass(frozen=True, slots=True)
class LexicalMatch:
    id: str
    start_token_ordinal: int
    end_token_ordinal: int
    surface: str
    display_lemma: str
    display_reading: str
    identity: LexicalFormIdentity
    meanings: tuple[str, ...]
    source: str
    jlpt_level: str | None
    jlpt_official: bool


@dataclass(frozen=True, slots=True)
class LinguisticAnalysis:
    tokens: tuple[LinguisticToken, ...]
    lexical_matches: tuple[LexicalMatch, ...]


class Tokenizer(Protocol):
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]: ...


class Dictionary(Protocol):
    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult: ...


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

    def analyze(self, region_id: str, text: str) -> LinguisticAnalysis:
        tokens: list[LinguisticToken] = []
        lexical_matches: list[LexicalMatch] = []
        for index, (surface, lemma, reading, part_of_speech) in enumerate(
            self._tokenizer.tokenize(text)
        ):
            token = LinguisticToken(
                id=f"{region_id}:token:{index}",
                surface=surface,
                lemma=lemma,
                reading=reading,
                part_of_speech=part_of_speech,
            )
            tokens.append(token)
            entry = self._dictionary.lookup_candidates(lemma, reading).unique_entry
            if entry is None:
                continue
            lexical_matches.append(
                LexicalMatch(
                    id=f"{region_id}:lexical:{index}",
                    start_token_ordinal=index,
                    end_token_ordinal=index + 1,
                    surface=surface,
                    display_lemma=lemma,
                    display_reading=reading,
                    identity=entry.identity,
                    meanings=entry.meanings,
                    source=entry.source,
                    jlpt_level=entry.jlpt_level,
                    jlpt_official=entry.jlpt_official,
                )
            )
        return LinguisticAnalysis(tokens=tuple(tokens), lexical_matches=tuple(lexical_matches))
