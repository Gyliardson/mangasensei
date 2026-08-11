"""Runtime composition and bounded service loops."""

from __future__ import annotations

import asyncio
import os
import socket
from decimal import Decimal
from typing import Protocol

from mangasensei.application.pdf_imports import PdfImportCoordinator
from mangasensei.config import Settings
from mangasensei.gemini.adapter import GoogleGenAiAdapter
from mangasensei.infrastructure.database.session import create_database
from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.jmdict_glosses import LocalizedJmdictGlossResolver
from mangasensei.linguistics.runtime_glosses import LazyJmdictGlossPackProvider
from mangasensei.linguistics.service import LinguisticService
from mangasensei.linguistics.sudachi import SudachiTokenizer
from mangasensei.ocr.adapter.manga_image_translator import MangaImageTranslatorEngine
from mangasensei.ocr.models.downloader import verify_models
from mangasensei.pdf_imports.renderer import PdfRenderer
from mangasensei.pdf_imports.spool import PdfSpool
from mangasensei.storage.images import ImageValidator
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.dictionary_projection import DictionaryProjectionWorker
from mangasensei.workers.retention import RetentionJanitor


class WorkerCycle(Protocol):
    async def run_once(self) -> bool: ...


class RetentionCycle(Protocol):
    async def run_once(self) -> int: ...


async def run_worker_loop(worker: WorkerCycle, *, poll_seconds: float, once: bool = False) -> None:
    while True:
        processed = await worker.run_once()
        if once:
            return
        if not processed:
            await asyncio.sleep(poll_seconds)


async def run_retention_loop(
    janitor: RetentionCycle, *, poll_seconds: float, once: bool = False
) -> None:
    while True:
        await janitor.run_once()
        if once:
            return
        await asyncio.sleep(poll_seconds)


async def run_worker_process(settings: Settings, *, once: bool = False) -> None:
    verify_models(settings.model_cache)
    dictionary = JsonJmdictDictionary(settings.jmdict_path)
    gloss_provider = LazyJmdictGlossPackProvider(settings.jmdict_path, dictionary)
    database_url = settings.require_database_url()
    engine, sessions = create_database(database_url)
    gemini = _gemini_adapter(settings)
    worker = DictionaryProjectionWorker(
        sessions=sessions,
        storage=LocalFilesystemStorage(settings.storage_root),
        ocr=MangaImageTranslatorEngine(
            model_cache=settings.model_cache,
            device=settings.ocr_device,
        ),
        linguistics=LinguisticService(SudachiTokenizer(), dictionary),
        gemini=gemini,
        worker_id=_worker_id(),
        lease_seconds=settings.worker_lease_seconds,
        gloss_resolver=LocalizedJmdictGlossResolver(gloss_provider),
        gemini_model=settings.gemini_model,
        gemini_daily_budget=Decimal(str(settings.gemini_daily_budget_usd)),
        gemini_max_calls_per_page=settings.gemini_max_calls_per_page,
    )
    try:
        await run_worker_loop(
            worker,
            poll_seconds=settings.worker_poll_seconds,
            once=once,
        )
    finally:
        if gemini is not None:
            await gemini.close()
        await engine.dispose()


async def run_pdf_import_process(settings: Settings, *, once: bool = False) -> None:
    database_url, capability_peppers = settings.require_runtime_config()
    engine, sessions = create_database(database_url)
    coordinator = PdfImportCoordinator(
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
        worker_id=_worker_id(),
    )
    try:
        await run_worker_loop(
            coordinator,
            poll_seconds=settings.pdf_import_poll_seconds,
            once=once,
        )
    finally:
        await engine.dispose()


def run_pdf_renderer_process(settings: Settings, *, once: bool = False) -> None:
    PdfRenderer(settings).run_forever(once=once)


async def run_retention_process(settings: Settings, *, once: bool = False) -> None:
    database_url = settings.require_database_url()
    engine, sessions = create_database(database_url)
    janitor = RetentionJanitor(sessions, LocalFilesystemStorage(settings.storage_root))
    try:
        await run_retention_loop(
            janitor,
            poll_seconds=settings.retention_poll_seconds,
            once=once,
        )
    finally:
        await engine.dispose()


def _gemini_adapter(settings: Settings) -> GoogleGenAiAdapter | None:
    if settings.google_api_key is None:
        return None
    return GoogleGenAiAdapter(
        model=settings.gemini_model,
        api_key=settings.google_api_key.get_secret_value(),
    )


def _worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"[:128]
