from __future__ import annotations

import os

import pytest

from mangasensei.config import Settings
from mangasensei.domain.languages import StudyLanguage
from mangasensei.gemini.adapter import GoogleGenAiAdapter
from mangasensei.gemini.contracts import GeminiPageAnalysis
from mangasensei.gemini.service import PAGE_STUDY_PROMPT_VERSION, build_page_prompt


@pytest.mark.gemini_smoke
@pytest.mark.asyncio
async def test_real_gemini_interactions_production_shaped_structured_output() -> None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY is not configured; real Gemini smoke was not executed")

    settings = Settings(_env_file=None)
    adapter = GoogleGenAiAdapter(
        model=settings.gemini_model,
        api_key=api_key,
        timeout_seconds=30,
        max_attempts=1,
    )
    prompt = build_page_prompt(
        prompt_version=PAGE_STUDY_PROMPT_VERSION,
        regions={"synthetic-region-001": "テストです"},
        vocabulary_by_region={"synthetic-region-001": ()},
        study_language=StudyLanguage.ENGLISH,
    )
    try:
        result = await adapter.analyze(prompt=prompt, schema=GeminiPageAnalysis)
    finally:
        await adapter.close()

    assert len(result.regions) == 1
    assert result.regions[0].region_id == "synthetic-region-001"
