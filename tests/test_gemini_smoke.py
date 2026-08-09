from __future__ import annotations

import os

import pytest
from pydantic import BaseModel, ConfigDict, Field

from mangasensei.config import Settings
from mangasensei.gemini.adapter import GoogleGenAiAdapter


class GeminiSmokeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(min_length=1, max_length=16)


@pytest.mark.gemini_smoke
@pytest.mark.asyncio
async def test_real_gemini_interactions_structured_output() -> None:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY is not configured; real Gemini smoke was not executed")

    settings = Settings(_env_file=None)
    adapter = GoogleGenAiAdapter(
        model=settings.gemini_model,
        api_key=api_key,
        timeout_seconds=30,
        max_attempts=1,
        max_output_tokens=128,
    )
    try:
        result = await adapter.analyze(
            prompt=(
                'Synthetic MangaSensei provider smoke. Return one JSON object with '
                'the field "status" set to "ok". Do not add other fields.'
            ),
            schema=GeminiSmokeResult,
        )
    finally:
        await adapter.close()

    assert result.status == "ok"
