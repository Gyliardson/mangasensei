"""Deterministic worker process for the browser-to-persistence full-stack gate."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from mangasensei.config import Settings
from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.service import LinguisticService
from mangasensei.linguistics.sudachi import SudachiTokenizer
from mangasensei.ocr.contracts import OcrImage, OcrProvenance, OcrRegionResult, OcrResult
from mangasensei.runtime import run_worker_loop
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker

REGION_ID = "5ca22b32-6834-59db-a183-428a557a22e8"
FULLSTACK_PROVENANCE = OcrProvenance(
    detector="fullstack-fixture",
    recognizer="fullstack-fixture",
    model_manifest_version="fullstack-v1",
    config_digest=hashlib.sha256(b"fullstack-deterministic-ocr-v1").digest(),
    upstream_repository="https://example.invalid/mangasensei/fullstack-ocr",
    upstream_commit="fullstack-ocr-v1",
)


class DeterministicFullStackOcr:
    async def analyze(self, image: OcrImage) -> OcrResult:
        # Keep the job non-terminal long enough for the browser's first status poll
        # to observe a real processing state rather than racing straight to completed.
        await asyncio.sleep(1.0)
        dimensions = PageDimensions(width=80, height=120)
        bbox = BoundingBox(x=10, y=20, width=40, height=60)
        return OcrResult(
            image_sha256=image.sha256,
            provenance=FULLSTACK_PROVENANCE,
            regions=(
                OcrRegionResult(
                    id=REGION_ID,
                    dimensions=dimensions,
                    bbox=bbox,
                    normalized_bbox=bbox.normalize(dimensions),
                    polygon=((10, 20), (50, 20), (50, 80), (10, 80)),
                    angle=0.0,
                    confidence=0.97,
                    japanese_text="猫です",
                    reading_order=0,
                    detector="fullstack-fixture",
                    recognizer="fullstack-fixture",
                    upstream_commit="fullstack-ocr-v1",
                ),
            ),
        )


class DeterministicFullStackGemini:
    """Replace only the paid external provider boundary with deterministic output."""

    async def analyze(
        self, *, prompt: str, schema: type[GeminiPageAnalysis]
    ) -> GeminiPageAnalysis:
        payload = json.loads(prompt)
        if not isinstance(payload, dict):
            raise ValueError("full-stack Gemini prompt must be an object")
        study_language = payload.get("study_language")
        if study_language not in {"pt-BR", "en"}:
            raise ValueError("full-stack Gemini prompt has an unsupported study language")
        raw_regions = payload.get("regions")
        if not isinstance(raw_regions, list):
            raise ValueError("full-stack Gemini prompt must contain regions")

        analyses: list[GeminiRegionAnalysis] = []
        for raw_region in raw_regions:
            if not isinstance(raw_region, dict):
                raise ValueError("full-stack Gemini region must be an object")
            region_id = raw_region.get("region_id")
            japanese_text = raw_region.get("japanese_text")
            candidates = raw_region.get("vocabulary_candidates")
            if not isinstance(region_id, str) or not isinstance(japanese_text, str):
                raise ValueError("full-stack Gemini region identity is invalid")
            if japanese_text != "猫です" or not isinstance(candidates, list):
                raise ValueError("full-stack Gemini received unexpected Japanese analysis")

            vocabulary_ids: list[str] = []
            for candidate in candidates:
                if not isinstance(candidate, dict) or not isinstance(candidate.get("id"), str):
                    raise ValueError("full-stack Gemini vocabulary candidate is invalid")
                vocabulary_ids.append(candidate["id"])

            if study_language == "en":
                translation = "It is a cat."
                explanation = "A polite nominal sentence."
                grammar_points = ("polite copula",)
            else:
                translation = "É um gato."
                explanation = "Frase nominal polida."
                grammar_points = ("cópula polida",)
            analyses.append(
                GeminiRegionAnalysis(
                    region_id=region_id,
                    translation=translation,
                    explanation=explanation,
                    grammar_points=grammar_points,
                    vocabulary_ids=tuple(vocabulary_ids),
                )
            )

        # Keep the provider step observable without introducing external I/O.
        await asyncio.sleep(0.4)
        return schema(regions=tuple(analyses))


async def main() -> None:
    settings = Settings(_env_file=None)
    database_url = settings.require_database_url()
    engine, sessions = create_database(database_url)
    dictionary = JsonJmdictDictionary(Path(__file__).parent / "fixtures" / "jmdict.json")
    worker = Worker(
        sessions=sessions,
        storage=LocalFilesystemStorage(settings.storage_root),
        ocr=DeterministicFullStackOcr(),
        linguistics=LinguisticService(SudachiTokenizer(), dictionary),
        gemini=DeterministicFullStackGemini(),
        gemini_model="fullstack-deterministic-provider",
        worker_id="fullstack-e2e-worker",
        lease_seconds=60,
    )
    try:
        await run_worker_loop(worker, poll_seconds=0.1)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
