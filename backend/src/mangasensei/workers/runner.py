"""Fenced page-analysis worker pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.domain.models import PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis
from mangasensei.gemini.service import GeminiAdapter, GeminiAnalysisService
from mangasensei.infrastructure.database.analysis_models import (
    GeminiAnalysisRecord,
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
    GeminiCostLedgerRecord,
    GeminiGrammarPointRecord,
    GeminiRegionAnalysisRecord,
    GeminiVocabularyLinkRecord,
    LinguisticMeaningRecord,
    LinguisticRunRecord,
    LinguisticTokenRecord,
    OcrRegionRecord,
    OcrRegionVertexRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.queue_repository import ClaimedJob, QueueRepository
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.linguistics.service import LinguisticService, LinguisticToken
from mangasensei.ocr.contracts import OcrEngine, OcrImage, OcrResult
from mangasensei.storage.local import LocalFilesystemStorage


_LOGGER = logging.getLogger(__name__)
_MAX_DIAGNOSTIC_CAUSES = 4
_MAX_DIAGNOSTIC_FRAMES = 8


class StaleLeaseError(RuntimeError):
    """The worker no longer owns the claimed job."""


class GeminiBudgetExceededError(RuntimeError):
    """The configured daily Gemini budget cannot reserve another call."""


def _public_error_code(exc: BaseException) -> str:
    """Map an internal failure to a stable public error code."""
    known = {
        GeminiBudgetExceededError: "gemini_budget_exceeded",
        UnicodeError: "linguistics_failed",
        MemoryError: "resource_exhausted",
        TimeoutError: "provider_timeout",
    }
    for kind, code in known.items():
        if isinstance(exc, kind):
            return code
    return "processing_failed"


def _safe_exception_context(exc: BaseException) -> str:
    """Describe exception types and source locations without exception messages or locals."""
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < _MAX_DIAGNOSTIC_CAUSES:
        seen.add(id(current))
        frames: list[str] = []
        traceback_cursor = current.__traceback__
        while traceback_cursor is not None:
            code = traceback_cursor.tb_frame.f_code
            frames.append(
                f"{pathlib.Path(code.co_filename).name}:{traceback_cursor.tb_lineno}:{code.co_name}"
            )
            traceback_cursor = traceback_cursor.tb_next
        locations = ">".join(frames[-_MAX_DIAGNOSTIC_FRAMES:]) or "no-traceback"
        parts.append(f"{type(current).__name__}@{locations}")
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return " <- ".join(parts)


class Worker:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        storage: LocalFilesystemStorage,
        ocr: OcrEngine,
        linguistics: LinguisticService,
        gemini: GeminiAdapter | None,
        worker_id: str,
        lease_seconds: int,
        gemini_model: str = "fake-or-configured",
        gemini_daily_budget: Decimal = Decimal("5.00"),
        gemini_reservation: Decimal = Decimal("0.52"),
        gemini_max_calls_per_page: int = 3,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._ocr = ocr
        self._linguistics = linguistics
        if gemini_max_calls_per_page < 1:
            raise ValueError("gemini_max_calls_per_page must be at least 1")
        self._gemini_max_calls_per_page = gemini_max_calls_per_page
        self._gemini = gemini
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._gemini_model = gemini_model
        self._gemini_daily_budget = gemini_daily_budget
        self._gemini_reservation = gemini_reservation

    async def run_once(self) -> bool:
        queue = QueueRepository(self._sessions)
        await queue.recover_expired_leases()
        claim = await queue.claim(worker_id=self._worker_id, lease_seconds=self._lease_seconds)
        if claim is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat_loop(claim))
        stage = "claim_transition"
        try:
            await self._transition(claim, "claimed", "processing_ocr")
            stage = "load_image"
            page, blob, content = await self._load_image(claim)
            stage = "ocr"
            ocr_result = await self._ocr.analyze(
                OcrImage(
                    content=content,
                    sha256=blob.sha256.hex(),
                    media_type=blob.media_type,
                    dimensions=PageDimensions(width=blob.width, height=blob.height),
                )
            )
            stage = "persist_ocr"
            region_ids = await self._persist_ocr(claim, blob, ocr_result)
            stage = "linguistics"
            linguistic_by_region = {
                region.id: self._linguistics.analyze(region.id, region.japanese_text)
                for region in ocr_result.regions
            }
            stage = "persist_linguistics"
            token_ids = await self._persist_linguistics(
                claim, ocr_result, region_ids, linguistic_by_region
            )
            if self._gemini is None:
                await self._complete_without_gemini(claim)
                return True
            stage = "reserve_gemini"
            call_id = await self._reserve_gemini_call(claim, page, ocr_result, linguistic_by_region)
            stage = "mark_gemini_sent"
            await self._mark_call_sent(call_id)
            vocabulary_ids = frozenset(
                token.dictionary_id
                for tokens in linguistic_by_region.values()
                for token in tokens
                if token.dictionary_id is not None
            )
            stage = "gemini"
            analysis = await GeminiAnalysisService(
                self._gemini, prompt_version="page-study-v1"
            ).analyze_page(
                regions={region.id: region.japanese_text for region in ocr_result.regions},
                vocabulary_ids=vocabulary_ids,
            )
            stage = "persist_gemini"
            await self._persist_gemini_and_complete(
                claim,
                call_id,
                analysis,
                region_ids,
                token_ids,
            )
        except StaleLeaseError:
            return True
        except Exception as exc:
            error_code = _public_error_code(exc)
            _LOGGER.error(
                "worker_pipeline_failed stage=%s job_id=%d attempt_no=%d fencing_token=%d "
                "error_code=%s exception_type=%s traceback=%s",
                stage,
                claim.job_id,
                claim.attempt_no,
                claim.fencing_token,
                error_code,
                type(exc).__name__,
                _safe_exception_context(exc),
            )
            await self._mark_retryable_failure(claim, error_code)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, StaleLeaseError):
                await heartbeat
        return True

    async def _heartbeat_loop(self, claim: ClaimedJob) -> None:
        interval = max(0.1, min(self._lease_seconds / 3, 30.0))
        queue = QueueRepository(self._sessions)
        while True:
            await asyncio.sleep(interval)
            renewed = await queue.heartbeat(
                job_id=claim.job_id,
                worker_id=claim.worker_id,
                fencing_token=claim.fencing_token,
                lease_seconds=self._lease_seconds,
            )
            if not renewed:
                raise StaleLeaseError

    async def _transition(self, claim: ClaimedJob, expected: str, target: str) -> None:
        async with self._sessions.begin() as session:
            result = await session.execute(
                update(JobRecord)
                .where(*_owned_job_predicates(claim, expected))
                .values(status=target, updated_at=func.now())
                .returning(JobRecord.id)
            )
            if result.scalar_one_or_none() is None:
                raise StaleLeaseError

    async def _load_image(self, claim: ClaimedJob) -> tuple[PageRecord, ImageBlobRecord, bytes]:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(PageRecord, ImageBlobRecord)
                    .join(ImageBlobRecord, ImageBlobRecord.id == PageRecord.image_blob_id)
                    .where(
                        PageRecord.id == claim.page_id,
                        PageRecord.expires_at > func.now(),
                        ImageBlobRecord.state == "ready",
                    )
                )
            ).one_or_none()
        if row is None:
            raise StaleLeaseError
        page, blob = row
        return page, blob, await self._storage.read(blob.storage_key)

    async def _persist_ocr(
        self, claim: ClaimedJob, blob: ImageBlobRecord, result: OcrResult
    ) -> dict[str, int]:
        if result.image_sha256 != blob.sha256.hex():
            raise ValueError("OCR result belongs to a different image")
        async with self._sessions.begin() as session:
            job = await _lock_owned_job(session, claim, "processing_ocr")
            run = OcrRunRecord(
                job_id=job.id,
                fencing_token=claim.fencing_token,
                detector=result.regions[0].detector if result.regions else "default",
                recognizer=result.regions[0].recognizer if result.regions else "48px",
                model_manifest_version="2026-08-07",
                config_digest=hashlib.sha256(b"default:48px:v1").digest(),
                upstream_repository="https://github.com/zyddnys/manga-image-translator",
                upstream_commit=(
                    result.regions[0].upstream_commit
                    if result.regions
                    else "95227a2bb0fd306cd4f0c104d57284026f991b3a"
                ),
                input_sha256=blob.sha256,
                width=blob.width,
                height=blob.height,
            )
            session.add(run)
            await session.flush()
            region_ids: dict[str, int] = {}
            for ordinal, region in enumerate(result.regions):
                record = OcrRegionRecord(
                    public_id=UUID(region.id),
                    ocr_run_id=run.id,
                    region_ordinal=ordinal,
                    reading_order=region.reading_order,
                    x=region.bbox.x,
                    y=region.bbox.y,
                    width=region.bbox.width,
                    height=region.bbox.height,
                    normalized_x=Decimal(str(region.normalized_bbox.x)),
                    normalized_y=Decimal(str(region.normalized_bbox.y)),
                    normalized_width=Decimal(str(region.normalized_bbox.width)),
                    normalized_height=Decimal(str(region.normalized_bbox.height)),
                    angle=Decimal(str(region.angle)),
                    confidence=Decimal(str(region.confidence)),
                    raw_text=region.japanese_text,
                    corrected_text=None,
                )
                session.add(record)
                await session.flush()
                region_ids[region.id] = record.id
                if region.polygon:
                    session.add_all(
                        [
                            OcrRegionVertexRecord(
                                region_id=record.id,
                                vertex_ordinal=index,
                                x=point[0],
                                y=point[1],
                            )
                            for index, point in enumerate(region.polygon)
                        ]
                    )
            job.status = "processing_linguistics"
            job.updated_at = func.now()
            return region_ids

    async def _persist_linguistics(
        self,
        claim: ClaimedJob,
        result: OcrResult,
        region_ids: dict[str, int],
        tokens_by_region: dict[str, tuple[LinguisticToken, ...]],
    ) -> dict[tuple[str, str], int]:
        input_digest = hashlib.sha256(
            json.dumps(
                [(region.id, region.japanese_text) for region in result.regions],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).digest()
        async with self._sessions.begin() as session:
            job = await _lock_owned_job(session, claim, "processing_linguistics")
            ocr_run_id = (
                await session.execute(
                    select(OcrRunRecord.id).where(
                        OcrRunRecord.job_id == job.id,
                        OcrRunRecord.fencing_token == claim.fencing_token,
                    )
                )
            ).scalar_one()
            run = LinguisticRunRecord(
                job_id=job.id,
                ocr_run_id=ocr_run_id,
                fencing_token=claim.fencing_token,
                tokenizer_name="SudachiPy",
                tokenizer_version="0.6.11",
                config_digest=hashlib.sha256(b"SplitMode.A").digest(),
                dictionary_name="JMdict",
                dictionary_version=self._linguistics.dictionary_version,
                dictionary_digest=self._linguistics.dictionary_digest,
                input_digest=input_digest,
            )
            session.add(run)
            await session.flush()
            token_ids: dict[tuple[str, str], int] = {}
            for region_id, tokens in tokens_by_region.items():
                cursor = 0
                for ordinal, token in enumerate(tokens):
                    start = cursor
                    cursor += len(token.surface)
                    stable_key = hashlib.sha256(
                        input_digest + region_id.encode() + ordinal.to_bytes(4, "big")
                    ).digest()
                    record = LinguisticTokenRecord(
                        linguistic_run_id=run.id,
                        region_id=region_ids[region_id],
                        token_ordinal=ordinal,
                        stable_key=stable_key,
                        start_offset=start,
                        end_offset=cursor,
                        surface=token.surface,
                        lemma=token.lemma,
                        reading=token.reading,
                        part_of_speech=token.part_of_speech,
                        dictionary_entry_id=token.dictionary_id,
                        dictionary_source=token.source,
                        jlpt_level=token.jlpt_level,
                        jlpt_official=token.jlpt_official,
                    )
                    session.add(record)
                    await session.flush()
                    if token.dictionary_id is not None:
                        token_ids[(region_id, token.dictionary_id)] = record.id
                    session.add_all(
                        [
                            LinguisticMeaningRecord(
                                token_id=record.id,
                                meaning_ordinal=index,
                                meaning=meaning,
                            )
                            for index, meaning in enumerate(token.meanings)
                        ]
                    )
            job.status = "processing_gemini" if self._gemini is not None else "completed"
            if self._gemini is None:
                _finish_job(job)
                await _finish_attempt(session, claim, "completed_without_gemini")
            job.updated_at = func.now()
            return token_ids

    async def _reserve_gemini_call(
        self,
        claim: ClaimedJob,
        page: PageRecord,
        result: OcrResult,
        linguistic: dict[str, tuple[LinguisticToken, ...]],
    ) -> int:
        request_digest = hashlib.sha256(
            json.dumps(
                {
                    "regions": [(region.id, region.japanese_text) for region in result.regions],
                    "vocabulary": sorted(
                        token.dictionary_id
                        for tokens in linguistic.values()
                        for token in tokens
                        if token.dictionary_id is not None
                    ),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).digest()
        today = datetime.now(UTC).date()
        async with self._sessions.begin() as session:
            await _lock_owned_job(session, claim, "processing_gemini")
            await session.execute(
                insert(GeminiBudgetBucketRecord)
                .values(
                    budget_date=today,
                    currency="USD",
                    limit_amount=self._gemini_daily_budget,
                    reserved_amount=Decimal("0"),
                    actual_amount=Decimal("0"),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        GeminiBudgetBucketRecord.budget_date,
                        GeminiBudgetBucketRecord.currency,
                    ]
                )
            )
            bucket = (
                await session.execute(
                    select(GeminiBudgetBucketRecord)
                    .where(
                        GeminiBudgetBucketRecord.budget_date == today,
                        GeminiBudgetBucketRecord.currency == "USD",
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if (
                bucket.reserved_amount + bucket.actual_amount + self._gemini_reservation
                > bucket.limit_amount
            ):
                raise GeminiBudgetExceededError
            ordinal = (
                await session.execute(
                    select(func.coalesce(func.max(GeminiCallRecord.page_call_ordinal), 0)).where(
                        GeminiCallRecord.page_id == page.id
                    )
                )
            ).scalar_one() + 1
            if ordinal > self._gemini_max_calls_per_page:
                raise GeminiBudgetExceededError("maximum Gemini calls reached")
            bucket.reserved_amount += self._gemini_reservation
            call = GeminiCallRecord(
                page_id=page.id,
                job_id=claim.job_id,
                page_call_ordinal=ordinal,
                fencing_token=claim.fencing_token,
                model=self._gemini_model,
                prompt_version="page-study-v1",
                schema_version="v1",
                request_digest=request_digest,
                reserved_cost=self._gemini_reservation,
            )
            session.add(call)
            await session.flush()
            return call.id

    async def _mark_call_sent(self, call_id: int) -> None:
        async with self._sessions.begin() as session:
            await session.execute(
                update(GeminiCallRecord)
                .where(GeminiCallRecord.id == call_id, GeminiCallRecord.state == "reserved")
                .values(state="sent", sent_at=func.now())
            )

    async def _persist_gemini_and_complete(
        self,
        claim: ClaimedJob,
        call_id: int,
        analysis: GeminiPageAnalysis,
        region_ids: dict[str, int],
        token_ids: dict[tuple[str, str], int],
    ) -> None:
        response_payload = analysis.model_dump_json()
        today = datetime.now(UTC).date()
        async with self._sessions.begin() as session:
            job = await _lock_owned_job(session, claim, "processing_gemini")
            linguistic_run_id = (
                await session.execute(
                    select(LinguisticRunRecord.id).where(
                        LinguisticRunRecord.job_id == job.id,
                        LinguisticRunRecord.fencing_token == claim.fencing_token,
                    )
                )
            ).scalar_one()
            call = await session.get_one(GeminiCallRecord, call_id, with_for_update=True)
            persisted = GeminiAnalysisRecord(
                job_id=job.id,
                linguistic_run_id=linguistic_run_id,
                gemini_call_id=call.id,
                response_digest=hashlib.sha256(response_payload.encode()).digest(),
            )
            session.add(persisted)
            await session.flush()
            for region in analysis.regions:
                region_record = GeminiRegionAnalysisRecord(
                    analysis_id=persisted.id,
                    region_id=region_ids[region.region_id],
                    translation=region.translation,
                    explanation=region.explanation,
                )
                session.add(region_record)
                await session.flush()
                session.add_all(
                    [
                        GeminiGrammarPointRecord(
                            region_analysis_id=region_record.id,
                            grammar_ordinal=index,
                            label=grammar,
                        )
                        for index, grammar in enumerate(region.grammar_points)
                    ]
                )
                for vocabulary_id in region.vocabulary_ids:
                    token_id = token_ids.get((region.region_id, vocabulary_id))
                    if token_id is None:
                        raise ValueError("Gemini vocabulary is not associated with its region")
                    session.add(
                        GeminiVocabularyLinkRecord(
                            region_analysis_id=region_record.id,
                            token_id=token_id,
                        )
                    )
            bucket = (
                await session.execute(
                    select(GeminiBudgetBucketRecord)
                    .where(
                        GeminiBudgetBucketRecord.budget_date == today,
                        GeminiBudgetBucketRecord.currency == "USD",
                    )
                    .with_for_update()
                )
            ).scalar_one()
            bucket.reserved_amount -= call.reserved_cost
            bucket.actual_amount += call.reserved_cost
            call.state = "succeeded"
            call.finished_at = func.now()
            session.add(
                GeminiCostLedgerRecord(
                    gemini_call_id=call.id,
                    observation_key="conservative-reservation-v1",
                    pricing_version="reservation-upper-bound-v1",
                    usage_category="reserved_upper_bound",
                    token_quantity=1,
                    unit_rate=call.reserved_cost,
                    amount=call.reserved_cost,
                )
            )
            job.status = "completed"
            _finish_job(job)
            await _finish_attempt(session, claim, "completed")

    async def _complete_without_gemini(self, claim: ClaimedJob) -> None:
        del claim

    async def _mark_retryable_failure(self, claim: ClaimedJob, error_code: str) -> None:
        async with self._sessions.begin() as session:
            job = (
                await session.execute(
                    select(JobRecord)
                    .where(
                        JobRecord.id == claim.job_id,
                        JobRecord.worker_id == claim.worker_id,
                        JobRecord.fencing_token == claim.fencing_token,
                        JobRecord.status.in_(
                            (
                                "claimed",
                                "processing_ocr",
                                "processing_linguistics",
                                "processing_gemini",
                            )
                        ),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                return
            await _settle_open_gemini_calls(session, claim)
            exhausted = job.attempt_count >= job.max_attempts
            job.status = "failed" if exhausted else "retryable_failure"
            job.available_at = func.now() + func.make_interval(
                0, 0, 0, 0, 0, 0, min(300, 2**job.attempt_count)
            )
            job.error_code = error_code[:64]
            job.error_detail = "O processamento falhou sem expor conteúdo sensível."
            job.worker_id = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            job.updated_at = func.now()
            if exhausted:
                job.finished_at = func.now()
            await _finish_attempt(session, claim, "failed" if exhausted else "retryable_failure")


def _owned_job_predicates(claim: ClaimedJob, expected: str) -> tuple[Any, ...]:
    return (
        JobRecord.id == claim.job_id,
        JobRecord.worker_id == claim.worker_id,
        JobRecord.fencing_token == claim.fencing_token,
        JobRecord.status == expected,
        JobRecord.lease_expires_at > func.now(),
    )


async def _lock_owned_job(session: AsyncSession, claim: ClaimedJob, expected: str) -> JobRecord:
    job = (
        await session.execute(
            select(JobRecord).where(*_owned_job_predicates(claim, expected)).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None:
        raise StaleLeaseError
    return job


def _finish_job(job: JobRecord) -> None:
    job.worker_id = None
    job.heartbeat_at = None
    job.lease_expires_at = None
    job.finished_at = func.now()
    job.updated_at = func.now()


async def _finish_attempt(session: AsyncSession, claim: ClaimedJob, outcome: str) -> None:
    await session.execute(
        update(JobAttemptRecord)
        .where(
            JobAttemptRecord.job_id == claim.job_id,
            JobAttemptRecord.attempt_no == claim.attempt_no,
            JobAttemptRecord.fencing_token == claim.fencing_token,
        )
        .values(ended_at=func.now(), outcome=outcome)
    )


async def _settle_open_gemini_calls(session: AsyncSession, claim: ClaimedJob) -> None:
    calls = (
        await session.execute(
            select(GeminiCallRecord)
            .where(
                GeminiCallRecord.job_id == claim.job_id,
                GeminiCallRecord.fencing_token == claim.fencing_token,
                GeminiCallRecord.state.in_(("reserved", "sent")),
            )
            .with_for_update()
        )
    ).scalars()
    for call in calls:
        bucket = (
            await session.execute(
                select(GeminiBudgetBucketRecord)
                .where(
                    GeminiBudgetBucketRecord.budget_date == call.created_at.date(),
                    GeminiBudgetBucketRecord.currency == "USD",
                )
                .with_for_update()
            )
        ).scalar_one()
        bucket.reserved_amount = max(Decimal("0"), bucket.reserved_amount - call.reserved_cost)
        if call.state == "sent":
            bucket.actual_amount += call.reserved_cost
            call.state = "unknown"
            session.add(
                GeminiCostLedgerRecord(
                    gemini_call_id=call.id,
                    observation_key="uncertain-request-upper-bound-v1",
                    pricing_version="reservation-upper-bound-v1",
                    usage_category="unknown_upper_bound",
                    token_quantity=1,
                    unit_rate=call.reserved_cost,
                    amount=call.reserved_cost,
                )
            )
        else:
            call.state = "failed"
        call.finished_at = func.now()
