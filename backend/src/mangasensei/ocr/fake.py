"""Deterministic OCR fake for tests and dependency-isolated development."""

from __future__ import annotations

from dataclasses import dataclass

from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult


@dataclass(frozen=True, slots=True)
class FakeOcrEngine:
    regions: tuple[OcrRegionResult, ...] = ()

    async def analyze(self, image: OcrImage) -> OcrResult:
        return OcrResult(image_sha256=image.sha256, regions=self.regions)
