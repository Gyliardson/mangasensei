"""Application service that constrains Gemini to known deterministic lexical matches."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from mangasensei.domain.languages import DEFAULT_STUDY_LANGUAGE, StudyLanguage
from mangasensei.gemini.contracts import GeminiPageAnalysis
from mangasensei.linguistics.service import LinguisticAnalysis

PAGE_STUDY_PROMPT_VERSION = "page-study-v4"


@dataclass(frozen=True, slots=True)
class GeminiVocabularyCandidate:
    """Minimal resolved local lexical context exposed to optional Gemini enrichment."""

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
    analyses_by_region: Mapping[str, LinguisticAnalysis],
) -> dict[str, tuple[GeminiVocabularyCandidate, ...]]:
    """Build one Gemini candidate per canonical lexical identity and region."""

    result: dict[str, tuple[GeminiVocabularyCandidate, ...]] = {}
    for region_id, analysis in analyses_by_region.items():
        seen_ids: set[str] = set()
        candidates: list[GeminiVocabularyCandidate] = []
        for match in analysis.lexical_matches:
            candidate_id = match.identity.transport_id
            if candidate_id in seen_ids:
                continue
            seen_ids.add(candidate_id)
            candidates.append(
                GeminiVocabularyCandidate(
                    id=candidate_id,
                    surface=match.surface,
                    lemma=match.display_lemma,
                    reading=match.display_reading,
                )
            )
        result[region_id] = tuple(candidates)
    return result


def build_page_prompt(
    *,
    prompt_version: str,
    regions: Mapping[str, str],
    vocabulary_by_region: Mapping[str, Sequence[GeminiVocabularyCandidate]],
    study_language: StudyLanguage = DEFAULT_STUDY_LANGUAGE,
) -> str:
    """Serialize only OCR text, the explicit study language and minimal lexical candidates."""

    payload = {
        "prompt_version": prompt_version,
        "study_language": study_language.value,
        "instructions": (
            "Return contextual translation, explanation, grammar points and only stable "
            "vocabulary identifiers listed for that same region. Write every translation, "
            "explanation and grammar-point label in exactly the requested study_language. "
            "Return exactly one analysis for every requested region. Never invent OCR text "
            "or identifiers."
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
        study_language: StudyLanguage = DEFAULT_STUDY_LANGUAGE,
    ) -> GeminiPageAnalysis:
        prompt = build_page_prompt(
            prompt_version=self._prompt_version,
            regions=regions,
            vocabulary_by_region=vocabulary_by_region,
            study_language=study_language,
        )
        result = await self._adapter.analyze(prompt=prompt, schema=GeminiPageAnalysis)
        known_regions = frozenset(regions)
        returned_region_ids = tuple(analysis.region_id for analysis in result.regions)
        for region_id in returned_region_ids:
            if region_id not in known_regions:
                raise UnknownRegionError(region_id)
        if Counter(returned_region_ids) != Counter(regions.keys()):
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
