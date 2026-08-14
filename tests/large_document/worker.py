"""Fast deterministic Slice E1 worker over the real Page worker pipeline."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from mangasensei.config import Settings
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.service import LinguisticService
from mangasensei.linguistics.sudachi import SudachiTokenizer
from mangasensei.ocr.contracts import OcrImage, OcrProvenance, OcrResult
from mangasensei.runtime import run_worker_loop
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import Worker
from tests.large_document.generator import PAGE_HEIGHT, PAGE_WIDTH, generate_pages

LARGE_DOCUMENT_PROVENANCE = OcrProvenance(
    detector="large-document-e1",
    recognizer="large-document-e1",
    model_manifest_version="large-document-e1-v1",
    config_digest=hashlib.sha256(b"large-document-e1-deterministic-empty-ocr-v1").digest(),
    upstream_repository="https://example.invalid/mangasensei/large-document-e1",
    upstream_commit="large-document-e1-v1",
)


class DeterministicLargeDocumentOcr:
    """Replace only the external OCR boundary and validate the persisted source bytes."""

    def __init__(self) -> None:
        self._ordinal_by_sha256 = {page.sha256: page.ordinal for page in generate_pages()}

    async def analyze(self, image: OcrImage) -> OcrResult:
        actual_sha256 = hashlib.sha256(image.content).hexdigest()
        if actual_sha256 != image.sha256:
            raise ValueError("persisted image bytes do not match the ImageBlob digest")
        if image.sha256 not in self._ordinal_by_sha256:
            raise ValueError("large-document worker received an image outside the frozen workload")
        if image.media_type != "image/png":
            raise ValueError("large-document worker expected image/png")
        if image.dimensions.width != PAGE_WIDTH or image.dimensions.height != PAGE_HEIGHT:
            raise ValueError("large-document worker received unexpected page dimensions")
        return OcrResult(
            image_sha256=image.sha256,
            provenance=LARGE_DOCUMENT_PROVENANCE,
            regions=(),
        )


async def main() -> None:
    settings = Settings(_env_file=None)
    engine, sessions = create_database(settings.require_database_url())
    dictionary_path = Path(__file__).parents[1] / "fullstack" / "fixtures" / "jmdict.json"
    dictionary = JsonJmdictDictionary(dictionary_path)
    worker = Worker(
        sessions=sessions,
        storage=LocalFilesystemStorage(settings.storage_root),
        ocr=DeterministicLargeDocumentOcr(),
        linguistics=LinguisticService(SudachiTokenizer(), dictionary),
        gemini=None,
        worker_id="large-document-e1-worker",
        lease_seconds=60,
    )
    try:
        await run_worker_loop(worker, poll_seconds=0.05)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
