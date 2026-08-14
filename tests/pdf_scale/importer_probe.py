from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from pathlib import Path
from typing import Any

from mangasensei.application.pdf_imports import PdfImportCoordinator, _ClaimedImport
from mangasensei.config import Settings
from mangasensei.infrastructure.database.session import create_database
from mangasensei.pdf_imports.contracts import PdfRasterManifest
from mangasensei.pdf_imports.spool import PdfSpool
from mangasensei.storage.images import ImageValidator, ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage

_EVENTS = Path("/app/var/pdf-spool/e3-importer-probe.jsonl")


def _record(value: dict[str, Any]) -> None:
    with _EVENTS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class ProbeCoordinator(PdfImportCoordinator):
    async def _process(self, claim: _ClaimedImport) -> None:
        started = time.perf_counter()
        try:
            await super()._process(claim)
        finally:
            _record(
                {
                    "phase": "overall",
                    "importId": str(claim.public_id),
                    "fencingToken": claim.fencing_token,
                    "elapsedSeconds": time.perf_counter() - started,
                }
            )

    def _validate_manifest(
        self,
        claim: _ClaimedImport,
        manifest: PdfRasterManifest,
    ) -> tuple[ValidatedImage, ...]:
        started = time.perf_counter()
        try:
            return super()._validate_manifest(claim, manifest)
        finally:
            _record(
                {
                    "phase": "manifestValidation",
                    "importId": str(claim.public_id),
                    "fencingToken": claim.fencing_token,
                    "elapsedSeconds": time.perf_counter() - started,
                }
            )

    async def _commit_document(
        self,
        claim: _ClaimedImport,
        manifest: PdfRasterManifest,
        images: tuple[ValidatedImage, ...],
    ) -> None:
        started = time.perf_counter()
        try:
            await super()._commit_document(claim, manifest, images)
        finally:
            _record(
                {
                    "phase": "commit",
                    "importId": str(claim.public_id),
                    "fencingToken": claim.fencing_token,
                    "elapsedSeconds": time.perf_counter() - started,
                }
            )


async def _run() -> None:
    settings = Settings()
    database_url, capability_peppers = settings.require_runtime_config()
    engine, sessions = create_database(database_url)
    coordinator = ProbeCoordinator(
        sessions=sessions,
        storage=LocalFilesystemStorage(settings.storage_root),
        spool=PdfSpool(settings.pdf_spool_root),
        image_validator=ImageValidator(
            max_bytes=settings.max_upload_bytes,
            max_pixels=settings.max_image_pixels,
            max_side=settings.max_image_side,
        ),
        settings=settings,
        idempotency_pepper=capability_peppers[0],
        worker_id=f"{socket.gethostname()}-{os.getpid()}"[:128],
    )
    try:
        while True:
            processed = await coordinator.run_once()
            if not processed:
                await asyncio.sleep(settings.pdf_import_poll_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run())
