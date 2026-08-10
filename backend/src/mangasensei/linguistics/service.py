"""Ports and orchestration for deterministic linguistic and lexical analysis."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

MAX_LEXICAL_SPAN_TOKENS = 4


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
class LexicalHypothesis:
    """One bounded dictionary lookup hypothesis aligned to canonical A-token ordinals."""

    start_token_ordinal: int
    end_token_ordinal: int
    lemma: str
    reading: str


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


@runtime_checkable
class LexicalHypothesisProvider(Protocol):
    def lexical_hypotheses(
        self,
        text: str,
        *,
        max_span_tokens: int,
    ) -> tuple[LexicalHypothesis, ...]: ...


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
        tokens = tuple(
            LinguisticToken(
                id=f"{region_id}:token:{index}",
                surface=surface,
                lemma=lemma,
                reading=reading,
                part_of_speech=part_of_speech,
            )
            for index, (surface, lemma, reading, part_of_speech) in enumerate(
                self._tokenizer.tokenize(text)
            )
        )
        hypotheses = self._lexical_hypotheses(text, tokens)
        lexical_matches: list[LexicalMatch] = []
        seen_occurrences: set[tuple[int, int, LexicalFormIdentity]] = set()
        for hypothesis in hypotheses:
            entry = self._dictionary.lookup_candidates(
                hypothesis.lemma,
                hypothesis.reading,
            ).unique_entry
            if entry is None:
                continue
            occurrence_key = (
                hypothesis.start_token_ordinal,
                hypothesis.end_token_ordinal,
                entry.identity,
            )
            if occurrence_key in seen_occurrences:
                continue
            seen_occurrences.add(occurrence_key)
            lexical_matches.append(
                LexicalMatch(
                    id=_match_id(region_id, hypothesis, entry.identity),
                    start_token_ordinal=hypothesis.start_token_ordinal,
                    end_token_ordinal=hypothesis.end_token_ordinal,
                    surface="".join(
                        token.surface
                        for token in tokens[
                            hypothesis.start_token_ordinal : hypothesis.end_token_ordinal
                        ]
                    ),
                    display_lemma=hypothesis.lemma,
                    display_reading=hypothesis.reading,
                    identity=entry.identity,
                    meanings=entry.meanings,
                    source=entry.source,
                    jlpt_level=entry.jlpt_level,
                    jlpt_official=entry.jlpt_official,
                )
            )
        lexical_matches.sort(
            key=lambda match: (
                match.start_token_ordinal,
                -(match.end_token_ordinal - match.start_token_ordinal),
                match.identity,
            )
        )
        return LinguisticAnalysis(tokens=tokens, lexical_matches=tuple(lexical_matches))

    def _lexical_hypotheses(
        self,
        text: str,
        tokens: tuple[LinguisticToken, ...],
    ) -> tuple[LexicalHypothesis, ...]:
        hypotheses = [
            LexicalHypothesis(index, index + 1, token.lemma, token.reading)
            for index, token in enumerate(tokens)
        ]
        hypotheses.extend(_surface_span_hypotheses(tokens))
        if isinstance(self._tokenizer, LexicalHypothesisProvider):
            hypotheses.extend(
                self._tokenizer.lexical_hypotheses(
                    text,
                    max_span_tokens=MAX_LEXICAL_SPAN_TOKENS,
                )
            )

        seen: set[LexicalHypothesis] = set()
        ordered: list[LexicalHypothesis] = []
        for hypothesis in hypotheses:
            _validate_hypothesis(hypothesis, len(tokens))
            if hypothesis in seen:
                continue
            seen.add(hypothesis)
            ordered.append(hypothesis)
        return tuple(ordered)


def _surface_span_hypotheses(
    tokens: tuple[LinguisticToken, ...],
) -> tuple[LexicalHypothesis, ...]:
    hypotheses: list[LexicalHypothesis] = []
    for start in range(len(tokens)):
        max_end = min(len(tokens), start + MAX_LEXICAL_SPAN_TOKENS)
        for end in range(start + 2, max_end + 1):
            span = tokens[start:end]
            hypotheses.append(
                LexicalHypothesis(
                    start_token_ordinal=start,
                    end_token_ordinal=end,
                    lemma="".join(token.surface for token in span),
                    reading="".join(token.reading for token in span),
                )
            )
    return tuple(hypotheses)


def _validate_hypothesis(hypothesis: LexicalHypothesis, token_count: int) -> None:
    span_length = hypothesis.end_token_ordinal - hypothesis.start_token_ordinal
    if not (
        0 <= hypothesis.start_token_ordinal < hypothesis.end_token_ordinal <= token_count
        and span_length <= MAX_LEXICAL_SPAN_TOKENS
    ):
        raise ValueError("lexical hypothesis is outside the bounded canonical token stream")
    if not hypothesis.lemma or not hypothesis.reading:
        raise ValueError("lexical hypothesis must have a lemma and reading")


def _match_id(
    region_id: str,
    hypothesis: LexicalHypothesis,
    identity: LexicalFormIdentity,
) -> str:
    if hypothesis.end_token_ordinal == hypothesis.start_token_ordinal + 1:
        return f"{region_id}:lexical:{hypothesis.start_token_ordinal}"
    return (
        f"{region_id}:lexical:{hypothesis.start_token_ordinal}:"
        f"{hypothesis.end_token_ordinal}:{identity.transport_id}"
    )
