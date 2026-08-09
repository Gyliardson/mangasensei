"""Application service that constrains Gemini to known deterministic IDs."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from mangasensei.gemini.contracts import GeminiPageAnalysis
from mangasensei.linguistics.service import LinguisticToken

PAGE_STUDY_PROMPT_VERSION = "page-study-v2"


@dataclass(frozen=True, slots=True)
class GeminiVocabularyCandidate:
    """Minimal local lexical context exposed to optional Gemini enrichment."""

    id: str
    surface: str
    lemma: str
    reading: str


class GeminiAdapter(Protocol):
    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis: ...


class UnknownRegionError(ValueError):
    """Gemini returned an identifier that was not present in its input."""


class UnknownVocabularyError(ValueError):
    """Gemini associated vocabulary that deterministic analysis did not emit."""


class RegionCompletenessError(ValueError):
    """Gemini did not return exactly one analysis for every requested region."""


def build_vocabulary_candidates_by_region(
    tokens_by_region: Mapping[str, Sequence[LinguisticToken]],
) -> dict[str, tuple[GeminiVocabularyCandidate, ...]]:
    """Build one candidate per stable dictionary ID and region.

    Repeated occurrences of the same dictionary entry in one region use the first
    local token as lexical context. The same dictionary entry in different regions
    remains an allowed candidate in each of those regions.
    """

    result: dict[str, tuple[GeminiVocabularyCandidate, ...]] = {}
    for region_id, tokens in tokens_by_region.items():
        seen_ids: set[str] = set()
        candidates: list[GeminiVocabularyCandidate] = []
        for token in tokens:
            if token.dictionary_id is None or token.dictionary_id in seen_ids:
                continue
            seen_ids.add(token.dictionary_id)
            candidates.append(
                GeminiVocabularyCandidate(
                    id=token.dictionary_id,
                    surface=token.surface,
                    lemma=token.lemma,
                    reading=token.reading,
                )
            )
        result[region_id] = tuple(candidates)
    return result


def build_page_prompt(
    *,
    prompt_version: str,
    regions: Mapping[str, str],
    vocabulary_by_region: Mapping[str, Sequence[GeminiVocabularyCandidate]],
) -> str:
    """Serialize only OCR text and minimal deterministic lexical candidates."""

    payload = {
        "prompt_version": prompt_version,
        "instructions": (
            "Return contextual translation, explanation, grammar points and only stable "
            "vocabulary identifiers listed for that same region. Return exactly one analysis "
            "for every requested region. Never invent OCR text or identifiers."
        ),
        "regions": [
            {
                "region_id": region_id,
                "japanese_text": text,
                "vocabulary_candidates": [
                    {
                        "id": candidate.id,
                        "surface": candidate.surface,
                        "lemma": candidate.lemma,
                        "reading": candidate.reading,
                    }
                    for candidate in vocabulary_by_region.get(region_id, ())
                ],
            }
            for region_id, text in regions.items()
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class GeminiAnalysisService:
    def __init__(self, adapter: GeminiAdapter, *, prompt_version: str) -> None:
        self._adapter = adapter
        self._prompt_version = prompt_version

    async def analyze_page(
        self,
        *,
        regions: Mapping[str, str],
        vocabulary_by_region: Mapping[str, Sequence[GeminiVocabularyCandidate]],
    ) -> GeminiPageAnalysis:
        prompt = build_page_prompt(
            prompt_version=self._prompt_version,
            regions=regions,
            vocabulary_by_region=vocabulary_by_region,
        )
        result = await self._adapter.analyze(prompt=prompt, schema=GeminiPageAnalysis)
        known_regions = frozenset(regions)
        returned_region_ids = tuple(analysis.region_id for analysis in result.regions)
        for region_id in returned_region_ids:
            if region_id not in known_regions:
                raise UnknownRegionError(region_id)
        if Counter(returned_region_ids) != Counter(regions):
            raise RegionCompletenessError(
                "Gemini response must contain exactly one analysis per requested region"
            )
        for analysis in result.regions:
            allowed_vocabulary = frozenset(
                candidate.id for candidate in vocabulary_by_region.get(analysis.region_id, ())
            )
            unknown_vocabulary = frozenset(analysis.vocabulary_ids) - allowed_vocabulary
            if unknown_vocabulary:
                raise UnknownVocabularyError(",".join(sorted(unknown_vocabulary)))
        return result
