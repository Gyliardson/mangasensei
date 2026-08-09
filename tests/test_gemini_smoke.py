from __future__ import annotations

import os
from typing import TypeVar

import pytest
from pydantic import BaseModel, ConfigDict, Field

from mangasensei.config import Settings
from mangasensei.domain.languages import StudyLanguage
from mangasensei.gemini.adapter import (
    GeminiProviderError,
    GeminiResponseError,
    GoogleGenAiAdapter,
)
from mangasensei.gemini.contracts import GeminiPageAnalysis
from mangasensei.gemini.service import PAGE_STUDY_PROMPT_VERSION, build_page_prompt

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class GeminiSmokeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=16)


def _require_api_key() -> str:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY is not configured; real Gemini smoke was not executed")
    return api_key


async def _analyze_variant(
    *,
    prompt: str,
    schema: type[SchemaT],
    max_output_tokens: int,
) -> SchemaT:
    settings = Settings(_env_file=None)
    adapter = GoogleGenAiAdapter(
        model=settings.gemini_model,
        api_key=_require_api_key(),
        timeout_seconds=30,
        max_attempts=1,
        max_output_tokens=max_output_tokens,
    )
    try:
        try:
            return await adapter.analyze(prompt=prompt, schema=schema)
        except GeminiProviderError as exc:
            cause = exc.__cause__
            status = getattr(cause, "status_code", getattr(cause, "code", None))
            provider_type = type(cause).__name__ if cause is not None else "unknown"
            raise AssertionError(
                f"provider request rejected: status={status} type={provider_type}"
            ) from None
        except GeminiResponseError as exc:
            raise AssertionError(f"provider response contract failed: {type(exc).__name__}") from None
    finally:
        await adapter.close()


_MINIMAL_PROMPT = (
    'Synthetic MangaSensei provider smoke. Return one JSON object with the field "status" '
    'set to "ok". Do not add other fields.'
)

_PAGE_SCHEMA_PROMPT = (
    "Synthetic MangaSensei provider diagnostic. Return exactly one regions item. "
    'Use region_id "synthetic-region-001", translation "Test.", explanation "Test.", '
    "and empty grammar_points and vocabulary_ids arrays."
)


def _production_shaped_prompt() -> str:
    return build_page_prompt(
        prompt_version=PAGE_STUDY_PROMPT_VERSION,
        regions={"synthetic-region-001": "テストです"},
        vocabulary_by_region={"synthetic-region-001": ()},
        study_language=StudyLanguage.ENGLISH,
    )


@pytest.mark.gemini_smoke
@pytest.mark.asyncio
async def test_real_gemini_interactions_structured_output() -> None:
    result = await _analyze_variant(
        prompt=_MINIMAL_PROMPT,
        schema=GeminiSmokeResult,
        max_output_tokens=128,
    )

    assert result.status == "ok"


@pytest.mark.gemini_smoke
@pytest.mark.asyncio
async def test_real_gemini_accepts_production_output_token_limit() -> None:
    result = await _analyze_variant(
        prompt=_MINIMAL_PROMPT,
        schema=GeminiSmokeResult,
        max_output_tokens=16_384,
    )

    assert result.status == "ok"


@pytest.mark.gemini_smoke
@pytest.mark.asyncio
async def test_real_gemini_accepts_page_analysis_schema() -> None:
    result = await _analyze_variant(
        prompt=_PAGE_SCHEMA_PROMPT,
        schema=GeminiPageAnalysis,
        max_output_tokens=128,
    )

    assert len(result.regions) == 1
    assert result.regions[0].region_id == "synthetic-region-001"


@pytest.mark.gemini_smoke
@pytest.mark.asyncio
async def test_real_gemini_accepts_production_shaped_request() -> None:
    result = await _analyze_variant(
        prompt=_production_shaped_prompt(),
        schema=GeminiPageAnalysis,
        max_output_tokens=16_384,
    )

    assert len(result.regions) == 1
    assert result.regions[0].region_id == "synthetic-region-001"
