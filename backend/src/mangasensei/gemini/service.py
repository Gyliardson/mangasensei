"""Application service that constrains Gemini to known deterministic IDs."""

from __future__ import annotations

import json
from typing import Protocol

from mangasensei.gemini.contracts import GeminiPageAnalysis


class GeminiAdapter(Protocol):
    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis: ...


class UnknownRegionError(ValueError):
    """Gemini returned an identifier that was not present in its input."""


class UnknownVocabularyError(ValueError):
    """Gemini associated vocabulary that deterministic analysis did not emit."""


class GeminiAnalysisService:
    def __init__(self, adapter: GeminiAdapter, *, prompt_version: str) -> None:
        self._adapter = adapter
        self._prompt_version = prompt_version

    async def analyze_page(
        self,
        *,
        regions: dict[str, str],
        vocabulary_ids: frozenset[str],
    ) -> GeminiPageAnalysis:
        payload = {
            "prompt_version": self._prompt_version,
            "instructions": (
                "Return contextual translation, explanation, grammar points and only the "
                "provided stable identifiers. Never invent OCR text or identifiers."
            ),
            "regions": [
                {"region_id": region_id, "japanese_text": text}
                for region_id, text in regions.items()
            ],
            "allowed_vocabulary_ids": sorted(vocabulary_ids),
        }
        result = await self._adapter.analyze(
            prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            schema=GeminiPageAnalysis,
        )
        known_regions = frozenset(regions)
        for analysis in result.regions:
            if analysis.region_id not in known_regions:
                raise UnknownRegionError(analysis.region_id)
            unknown_vocabulary = frozenset(analysis.vocabulary_ids) - vocabulary_ids
            if unknown_vocabulary:
                raise UnknownVocabularyError(",".join(sorted(unknown_vocabulary)))
        return result
