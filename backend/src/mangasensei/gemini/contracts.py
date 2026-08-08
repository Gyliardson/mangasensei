"""Strict structured-output contracts for Gemini."""

from pydantic import BaseModel, ConfigDict, Field


class GeminiContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GeminiRegionAnalysis(GeminiContract):
    region_id: str = Field(min_length=1, max_length=128)
    translation: str = Field(min_length=1, max_length=4_000)
    explanation: str = Field(min_length=1, max_length=8_000)
    grammar_points: tuple[str, ...] = Field(max_length=32)
    vocabulary_ids: tuple[str, ...] = Field(max_length=128)


class GeminiPageAnalysis(GeminiContract):
    regions: tuple[GeminiRegionAnalysis, ...] = Field(max_length=128)
