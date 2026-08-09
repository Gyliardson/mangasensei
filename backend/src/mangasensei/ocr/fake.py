"""Deterministic OCR fake for tests and dependency-isolated development."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from mangasensei.ocr.contracts import OcrImage, OcrProvenance, OcrRegionResult, OcrResult

DEFAULT_FAKE_PROVENANCE = OcrProvenance(
    detector="fake",
    recognizer="fake",
    model_manifest_version="fake-v1",
    config_digest=hashlib.sha256(b"FakeOcrEngine:v1").digest(),
    upstream_repository="https://example.invalid/mangasensei/fake-ocr",
    upstream_commit="fake-ocr-v1",
)


@dataclass(frozen=True, slots=True)
class FakeOcrEngine:
    regions: tuple[OcrRegionResult, ...] = ()
    provenance: OcrProvenance = DEFAULT_FAKE_PROVENANCE

    async def analyze(self, image: OcrImage) -> OcrResult:
        return OcrResult(
            image_sha256=image.sha256,
            provenance=self.provenance,
            regions=self.regions,
        )
