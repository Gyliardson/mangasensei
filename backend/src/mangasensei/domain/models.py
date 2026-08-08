"""Immutable OCR and page domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainModel(BaseModel):
    """Shared strict configuration for values crossing module boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PageDimensions(DomainModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class NormalizedBoundingBox(DomainModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class BoundingBox(DomainModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)

    def normalize(self, dimensions: PageDimensions) -> NormalizedBoundingBox:
        return NormalizedBoundingBox(
            x=self.x / dimensions.width,
            y=self.y / dimensions.height,
            width=self.width / dimensions.width,
            height=self.height / dimensions.height,
        )


class OcrRegion(DomainModel):
    id: str = Field(min_length=1, max_length=128)
    page_dimensions: PageDimensions
    bbox: BoundingBox
    polygon: tuple[tuple[int, int], ...] | None
    angle: float = Field(ge=-180, le=180)
    confidence: float = Field(ge=0, le=1)
    raw_text: str = Field(min_length=1, max_length=10_000)
    corrected_text: str | None = Field(default=None, max_length=10_000)
    reading_order: int = Field(ge=0)
    detector: str = Field(min_length=1, max_length=64)
    recognizer: str = Field(min_length=1, max_length=64)
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    created_at: datetime

    @property
    def normalized_bbox(self) -> NormalizedBoundingBox:
        return self.bbox.normalize(self.page_dimensions)

    @model_validator(mode="after")
    def validate_geometry(self) -> OcrRegion:
        if (
            self.bbox.x + self.bbox.width > self.page_dimensions.width
            or self.bbox.y + self.bbox.height > self.page_dimensions.height
        ):
            raise ValueError("bounding box is outside page dimensions")
        if self.polygon is not None:
            if len(self.polygon) < 3:
                raise ValueError("polygon must have at least three points")
            for x, y in self.polygon:
                if not (
                    0 <= x <= self.page_dimensions.width and 0 <= y <= self.page_dimensions.height
                ):
                    raise ValueError("polygon is outside page dimensions")
        return self
