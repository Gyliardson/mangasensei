"""Immutable internal OCR interface."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from mangasensei.domain.models import BoundingBox, NormalizedBoundingBox, PageDimensions


class OcrContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class OcrImage(OcrContract):
    content: bytes = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    dimensions: PageDimensions


class OcrProvenance(OcrContract):
    detector: str = Field(min_length=1, max_length=64)
    recognizer: str = Field(min_length=1, max_length=64)
    model_manifest_version: str = Field(min_length=1, max_length=64)
    config_digest: bytes = Field(min_length=32, max_length=32)
    upstream_repository: str = Field(min_length=1)
    upstream_commit: str = Field(min_length=1, max_length=128)


class OcrRegionResult(OcrContract):
    id: str
    dimensions: PageDimensions
    bbox: BoundingBox
    normalized_bbox: NormalizedBoundingBox
    polygon: tuple[tuple[int, int], ...] | None
    angle: float = Field(ge=-180, le=180)
    confidence: float = Field(ge=0, le=1)
    japanese_text: str = Field(min_length=1)
    reading_order: int = Field(ge=0)
    detector: str
    recognizer: str
    upstream_commit: str


class OcrResult(OcrContract):
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: OcrProvenance
    regions: tuple[OcrRegionResult, ...] = Field(max_length=128)


class OcrEngine(Protocol):
    async def analyze(self, image: OcrImage) -> OcrResult: ...
