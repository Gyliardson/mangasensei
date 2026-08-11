"""Fenced page-analysis worker pipeline."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pathlib
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.domain.languages import (
    CONTENT_LANGUAGE,
    LOCAL_DICTIONARY_LANGUAGE,
    StudyLanguage,
)
from mangasensei.domain.models import PageDimensions
from mangasensei.gemini.contracts import GeminiPageAnalysis
from mangasensei.gemini.errors import GeminiProviderError, GeminiResponseError
from mangasensei.gemini.service import (
    PAGE_STUDY_PROMPT_VERSION,
    GeminiAdapter,
    GeminiAnalysisService,
    GeminiVocabularyCandidate,
    RegionCompletenessError,
    UnknownRegionError,
    UnknownVocabularyError,
    build_page_prompt,
    build_vocabulary_candidates_by_region,
)
from mangasensei.infrastructure.database.analysis_models import (
    GeminiAnalysisRecord,
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
    GeminiCostLedgerRecord,
    GeminiGrammarPointRecord,
    GeminiRegionAnalysisRecord,
    LinguisticRunRecord,
    LinguisticTokenRecord,
    OcrRegionRecord,
    OcrRegionVertexRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.gemini_accounting import (
    reconcile_abandoned_gemini_calls,
)
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.lexical_models import (
    GeminiLexicalVocabularyLinkRecord,
    LexicalMatchRecord,
    LexicalMeaningRecord,
)
from mangasensei.infrastructure.database.queue_repository import ClaimedJob, QueueRepository
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from mangasensei.linguistics.service import (
    LexicalFormIdentity,
    LinguisticAnalysis,
    LinguisticService,
)
from mangasensei.ocr.contracts import OcrEngine, OcrImage, OcrResult
from mangasensei.storage.local import LocalFilesystemStorage

_LOGGER = logging.getLogger(__name__)
_MAX_DIAGNOSTIC_CAUSES = 4
_MAX_DIAGNOSTIC_FRAMES = 8


class StaleLeaseError(RuntimeError):
    """The worker no longer owns the claimed job."""


class CancellationAcknowledgedError(StaleLeaseError):
    """The lease owner durably acknowledged a Document cancellation request."""


class GeminiBudgetExceededError(RuntimeError):
    """A configured Gemini budget or per-page call bound blocked another call."""


class GeminiDailyBudgetExceededError(GeminiBudgetExceededError):
    """The configured daily Gemini budget cannot reserve another call."""


class GeminiPageCallLimitExceededError(GeminiBudgetExceededError):
    """The configured per-page Gemini call allowance has been exhausted."""


@dataclass(frozen=True, slots=True)
class ReusableAnalysis:
    page: PageRecord
    linguistic_run_id: int
    regions: dict[str, str]
    vocabulary_by_region: dict[str, tuple[GeminiVocabularyCandidate, ...]]
    region_ids: dict[str, int]
    lexical_match_ids: dict[tuple[str, str], int]


def _public_error_code(exc: BaseException) -> str:
    """Map an internal failure to a stable public error code."""
    if isinstance(exc, GeminiProviderError):
        return "gemini_provider_failed"
    if isinstance(
        exc,
        (
            GeminiResponseError,
            RegionCompletenessError,
            UnknownRegionError,
            UnknownVocabularyError,
        ),
    ):
        return "gemini_response_invalid"
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


def _is_retryable_pipeline_failure(exc: BaseException) -> bool:
    """Keep transient/generated-output failures retryable and terminalize permanent bounds."""
    if isinstance(exc, GeminiProviderError):
        return exc.retryable
    if isinstance(exc, GeminiBudgetExceededError):
        return False
    if isinstance(
        exc,
        (
            GeminiResponseError,
            RegionCompletenessError,
            UnknownRegionError,
            UnknownVocabularyError,
        ),
    ):
        return True
    return True


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
        stage = "cancel_checkpoint"
        try:
            await self._checkpoint_cancellation(claim, "claimed")
            stage = "load_job_contract"
            job_kind, study_language = await self._load_job_contract(claim)
            if job_kind == "study_language_reprocess":
                stage = "study_language_reprocess"
                await self._run_study_language_reprocess(claim, study_language)
                return True

            stage = "claim_transition"
            await self._transition(claim, "claimed", "processing_ocr")
            await self._checkpoint_cancellation(claim, "processing_ocr")
            stage = "load_image"
            page, blob, content = await self._load_image(claim)
            await self._checkpoint_cancellation(claim, "processing_ocr")
            stage = "ocr"
            ocr_result = await self._ocr.analyze(
                OcrImage(
                    content=content,
                    sha256=blob.sha256.hex(),
                    media_type=blob.media_type,
                    dimensions=PageDimensions(width=blob.width, height=blob.height),
                )
            )
            await self._checkpoint_cancellation(claim, "processing_ocr")
            stage = "persist_ocr"
            region_ids = await self._persist_ocr(claim, blob, ocr_result)
            await self._checkpoint_cancellation(claim, "processing_linguistics")
            stage = "linguistics"
            linguistic_by_region = {
                region.id: self._linguistics.analyze(region.id, region.japanese_text)
                for region in ocr_result.regions
            }
            await self._checkpoint_cancellation(claim, "processing_linguistics")
            stage = "persist_linguistics"
            linguistic_run_id, lexical_match_ids = await self._persist_linguistics(
                claim, ocr_result, region_ids, linguistic_by_region
            )
            if self._gemini is None or not ocr_result.regions:
                await self._complete_without_gemini(claim)
                return True
            regions = {region.id: region.japanese_text for region in ocr_result.regions}
            vocabulary_by_region = build_vocabulary_candidates_by_region(linguistic_by_region)
            prompt = build_page_prompt(
                prompt_version=PAGE_STUDY_PROMPT_VERSION,
                regions=regions,
                vocabulary_by_region=vocabulary_by_region,
                study_language=study_language,
            )
            await self._checkpoint_cancellation(claim, "processing_gemini")
            stage = "reserve_gemini"
            call_id = await self._reserve_gemini_call(claim, page, prompt)
            stage = "mark_gemini_sent"
            await self._mark_call_sent(call_id)
            await self._checkpoint_cancellation(claim, "processing_gemini")
            stage = "gemini"
            analysis = await GeminiAnalysisService(
                self._gemini, prompt_version=PAGE_STUDY_PROMPT_VERSION
            ).analyze_page(
                regions=regions,
                vocabulary_by_region=vocabulary_by_region,
                study_language=study_language,
            )
            await self._checkpoint_cancellation(claim, "processing_gemini")
            stage = "persist_gemini"
            await self._persist_gemini_and_complete(
                claim,
                call_id,
                analysis,
                linguistic_run_id,
                region_ids,
                lexical_match_ids,
            )
        except StaleLeaseError:
            return True
        except Exception as exc:
            error_code = _public_error_code(exc)
            retryable = _is_retryable_pipeline_failure(exc)
            _LOGGER.error(
                "worker_pipeline_failed stage=%s job_id=%d attempt_no=%d fencing_token=%d "
                "error_code=%s retryable=%s exception_type=%s traceback=%s",
                stage,
                claim.job_id,
                claim.attempt_no,
                claim.fencing_token,
                error_code,
                retryable,
                type(exc).__name__,
                _safe_exception_context(exc),
            )
            await self._mark_failure(claim, error_code, retryable=retryable)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, StaleLeaseError):
                await heartbeat
        return True

    async def _load_job_contract(self, claim: ClaimedJob) -> tuple[str, StudyLanguage]:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(JobRecord.job_kind, JobRecord.study_language).where(
                        *_owned_job_predicates(claim, "claimed")
                    )
                )
            ).one_or_none()
        if row is None:
            raise StaleLeaseError
        return row.job_kind, StudyLanguage(row.study_language)

    async def _run_study_language_reprocess(
        self,
        claim: ClaimedJob,
        study_language: StudyLanguage,
    ) -> None:
        await self._checkpoint_cancellation(claim, "claimed")
        reusable = await self._load_reusable_analysis(claim)
        if self._gemini is None or not reusable.regions:
            await self._checkpoint_cancellation(claim, "claimed")
            await self._complete_reused_without_gemini(claim, reusable.linguistic_run_id)
            return
        await self._transition(claim, "claimed", "processing_gemini")
        await self._checkpoint_cancellation(claim, "processing_gemini")
        prompt = build_page_prompt(
            prompt_version=PAGE_STUDY_PROMPT_VERSION,
            regions=reusable.regions,
            vocabulary_by_region=reusable.vocabulary_by_region,
            study_language=study_language,
        )
        call_id = await self._reserve_gemini_call(claim, reusable.page, prompt)
        await self._mark_call_sent(call_id)
        await self._checkpoint_cancellation(claim, "processing_gemini")
        analysis = await GeminiAnalysisService(
            self._gemini, prompt_version=PAGE_STUDY_PROMPT_VERSION
        ).analyze_page(
            regions=reusable.regions,
            vocabulary_by_region=reusable.vocabulary_by_region,
            study_language=study_language,
        )
        await self._checkpoint_cancellation(claim, "processing_gemini")
        await self._persist_gemini_and_complete(
            claim,
            call_id,
            analysis,
            reusable.linguistic_run_id,
            reusable.region_ids,
            reusable.lexical_match_ids,
        )

    async def _load_reusable_analysis(self, claim: ClaimedJob) -> ReusableAnalysis:
        async with self._sessions() as session:
            page = (
                await session.execute(
                    select(PageRecord).where(
                        PageRecord.id == claim.page_id,
                        PageRecord.expires_at > func.now(),
                    )
                )
            ).scalar_one_or_none()
            if page is None:
                raise StaleLeaseError
            source = (
                await session.execute(
                    select(StudyResultRecord)
                    .join(JobRecord, JobRecord.id == StudyResultRecord.job_id)
                    .where(
                        JobRecord.page_id == claim.page_id,
                        JobRecord.status == "completed",
                    )
                    .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if source is None:
                raise RuntimeError("study-language reprocess requires a completed analysis")
            linguistic_run = await session.get_one(
                LinguisticRunRecord, source.linguistic_run_id
            )
            regions = (
                await session.execute(
                    select(OcrRegionRecord)
                    .where(OcrRegionRecord.ocr_run_id == linguistic_run.ocr_run_id)
                    .order_by(OcrRegionRecord.reading_order, OcrRegionRecord.id)
                )
            ).scalars().all()
            region_public_by_id = {region.id: str(region.public_id) for region in regions}
            region_ids = {str(region.public_id): region.id for region in regions}
            region_text = {
                str(region.public_id): region.corrected_text or region.raw_text
                for region in regions
            }
            lexical_matches = (
                await session.execute(
                    select(LexicalMatchRecord)
                    .where(LexicalMatchRecord.linguistic_run_id == linguistic_run.id)
                    .order_by(
                        LexicalMatchRecord.region_id,
                        LexicalMatchRecord.start_token_ordinal,
                        LexicalMatchRecord.end_token_ordinal.desc(),
                        LexicalMatchRecord.id,
                    )
                )
            ).scalars().all()

        vocabulary_lists: dict[str, list[GeminiVocabularyCandidate]] = {
            region_id: [] for region_id in region_text
        }
        seen_ids: dict[str, set[str]] = {region_id: set() for region_id in region_text}
        lexical_match_ids: dict[tuple[str, str], int] = {}
        for match in lexical_matches:
            region_id = region_public_by_id[match.region_id]
            identity = LexicalFormIdentity(
                dictionary_namespace=match.dictionary_namespace,
                entry_id=match.dictionary_entry_id,
                lemma=match.form_lemma,
                reading=match.form_reading,
            )
            candidate_id = identity.transport_id
            lexical_match_ids.setdefault((region_id, candidate_id), match.id)
            if candidate_id in seen_ids[region_id]:
                continue
            seen_ids[region_id].add(candidate_id)
            vocabulary_lists[region_id].append(
                GeminiVocabularyCandidate(
                    id=candidate_id,
                    surface=match.surface,
                    lemma=match.display_lemma,
                    reading=match.display_reading,
                )
            )
        vocabulary_by_region = {
            region_id: tuple(candidates)
            for region_id, candidates in vocabulary_lists.items()
        }
        return ReusableAnalysis(
            page=page,
            linguistic_run_id=linguistic_run.id,
            regions=region_text,
            vocabulary_by_region=vocabulary_by_region,
            region_ids=region_ids,
            lexical_match_ids=lexical_match_ids,
        )

    async def _complete_reused_without_gemini(
        self,
        claim: ClaimedJob,
        linguistic_run_id: int,
    ) -> None:
        async with self._sessions.begin() as session:
            job = await _lock_owned_job(session, claim, "claimed")
            _add_study_result(session, job, linguistic_run_id)
            job.status = "completed"
            _finish_job(job)
            await _finish_attempt(session, claim, "completed_reused_analysis")

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

    async def _checkpoint_cancellation(self, claim: ClaimedJob, expected: str) -> None:
        cancelled = False
        async with self._sessions.begin() as session:
            job = (
                await session.execute(
                    select(JobRecord)
                    .where(*_owned_job_identity_predicates(claim, expected))
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                raise StaleLeaseError
            if job.cancel_requested_at is not None:
                await reconcile_abandoned_gemini_calls(
                    session,
                    job_id=claim.job_id,
                    fencing_token=claim.fencing_token,
                )
                job.status = "cancelled"
                job.error_code = None
                job.error_detail = None
                _finish_job(job)
                await _finish_attempt(session, claim, "cancelled")
                cancelled = True
        if cancelled:
            raise CancellationAcknowledgedError

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
            provenance = result.provenance
            run = OcrRunRecord(
                job_id=job.id,
                fencing_token=claim.fencing_token,
                detector=provenance.detector,
                recognizer=provenance.recognizer,
                model_manifest_version=provenance.model_manifest_version,
                config_digest=provenance.config_digest,
                upstream_repository=provenance.upstream_repository,
                upstream_commit=provenance.upstream_commit,
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
        analyses_by_region: dict[str, LinguisticAnalysis],
    ) -> tuple[int, dict[tuple[str, str], int]]:
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
            lexical_match_ids: dict[tuple[str, str], int] = {}
            for region_id, analysis in analyses_by_region.items():
                cursor = 0
                for ordinal, token in enumerate(analysis.tokens):
                    start = cursor
                    cursor += len(token.surface)
                    stable_key = hashlib.sha256(
                        input_digest + region_id.encode() + ordinal.to_bytes(4, "big")
                    ).digest()
                    session.add(
                        LinguisticTokenRecord(
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
                        )
                    )
                for match in analysis.lexical_matches:
                    if not (
                        0
                        <= match.start_token_ordinal
                        < match.end_token_ordinal
                        <= len(analysis.tokens)
                    ):
                        raise ValueError("lexical match is outside the canonical token stream")
                    identity = match.identity
                    stable_key = hashlib.sha256(
                        input_digest
                        + region_id.encode()
                        + match.start_token_ordinal.to_bytes(4, "big")
                        + match.end_token_ordinal.to_bytes(4, "big")
                        + identity.transport_id.encode()
                    ).digest()
                    record = LexicalMatchRecord(
                        linguistic_run_id=run.id,
                        region_id=region_ids[region_id],
                        stable_key=stable_key,
                        start_token_ordinal=match.start_token_ordinal,
                        end_token_ordinal=match.end_token_ordinal,
                        surface=match.surface,
                        display_lemma=match.display_lemma,
                        display_reading=match.display_reading,
                        dictionary_namespace=identity.dictionary_namespace,
                        dictionary_entry_id=identity.entry_id,
                        form_lemma=identity.lemma,
                        form_reading=identity.reading,
                        dictionary_source=match.source,
                        jlpt_level=match.jlpt_level,
                        jlpt_official=match.jlpt_official,
                    )
                    session.add(record)
                    await session.flush()
                    candidate_id = identity.transport_id
                    lexical_match_ids.setdefault((region_id, candidate_id), record.id)
                    session.add_all(
                        [
                            LexicalMeaningRecord(
                                lexical_match_id=record.id,
                                meaning_ordinal=index,
                                meaning=meaning,
                            )
                            for index, meaning in enumerate(match.meanings)
                        ]
                    )
            use_gemini = self._gemini is not None and bool(result.regions)
            job.status = "processing_gemini" if use_gemini else "completed"
            if not use_gemini:
                _add_study_result(session, job, run.id)
                _finish_job(job)
                await _finish_attempt(session, claim, "completed_without_gemini")
            job.updated_at = func.now()
            return run.id, lexical_match_ids

    async def _reserve_gemini_call(
        self,
        claim: ClaimedJob,
        page: PageRecord,
        prompt: str,
    ) -> int:
        request_digest = hashlib.sha256(prompt.encode()).digest()
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
                raise GeminiDailyBudgetExceededError
            ordinal = (
                await session.execute(
                    select(func.coalesce(func.max(GeminiCallRecord.page_call_ordinal), 0)).where(
                        GeminiCallRecord.page_id == page.id
                    )
                )
            ).scalar_one() + 1
            if ordinal > self._gemini_max_calls_per_page:
                raise GeminiPageCallLimitExceededError
            bucket.reserved_amount += self._gemini_reservation
            call = GeminiCallRecord(
                page_id=page.id,
                job_id=claim.job_id,
                page_call_ordinal=ordinal,
                fencing_token=claim.fencing_token,
                model=self._gemini_model,
                prompt_version=PAGE_STUDY_PROMPT_VERSION,
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
        linguistic_run_id: int,
        region_ids: dict[str, int],
        lexical_match_ids: dict[tuple[str, str], int],
    ) -> None:
        response_payload = analysis.model_dump_json()
        today = datetime.now(UTC).date()
        async with self._sessions.begin() as session:
            job = await _lock_owned_job(session, claim, "processing_gemini")
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
                    lexical_match_id = lexical_match_ids.get((region.region_id, vocabulary_id))
                    if lexical_match_id is None:
                        raise ValueError("Gemini vocabulary is not associated with its region")
                    session.add(
                        GeminiLexicalVocabularyLinkRecord(
                            region_analysis_id=region_record.id,
                            lexical_match_id=lexical_match_id,
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
            _add_study_result(session, job, linguistic_run_id)
            job.status = "completed"
            _finish_job(job)
            await _finish_attempt(session, claim, "completed")

    async def _complete_without_gemini(self, claim: ClaimedJob) -> None:
        del claim

    async def _mark_failure(
        self,
        claim: ClaimedJob,
        error_code: str,
        *,
        retryable: bool,
    ) -> None:
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
            await reconcile_abandoned_gemini_calls(
                session,
                job_id=claim.job_id,
                fencing_token=claim.fencing_token,
            )
            if job.cancel_requested_at is not None:
                job.status = "cancelled"
                job.error_code = None
                job.error_detail = None
                _finish_job(job)
                await _finish_attempt(session, claim, "cancelled")
                return
            terminal = not retryable or job.attempt_count >= job.max_attempts
            job.status = "failed" if terminal else "retryable_failure"
            if not terminal:
                job.available_at = func.now() + func.make_interval(
                    0, 0, 0, 0, 0, 0, min(300, 2**job.attempt_count)
                )
            job.error_code = error_code[:64]
            job.error_detail = "O processamento falhou sem expor conteúdo sensível."
            job.worker_id = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            job.updated_at = func.now()
            if terminal:
                job.finished_at = func.now()
            await _finish_attempt(
                session,
                claim,
                "failed" if terminal else "retryable_failure",
            )


def _owned_job_identity_predicates(claim: ClaimedJob, expected: str) -> tuple[Any, ...]:
    return (
        JobRecord.id == claim.job_id,
        JobRecord.worker_id == claim.worker_id,
        JobRecord.fencing_token == claim.fencing_token,
        JobRecord.status == expected,
        JobRecord.lease_expires_at > func.now(),
    )


def _owned_job_predicates(claim: ClaimedJob, expected: str) -> tuple[Any, ...]:
    return (
        *_owned_job_identity_predicates(claim, expected),
        JobRecord.cancel_requested_at.is_(None),
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


def _add_study_result(
    session: AsyncSession,
    job: JobRecord,
    linguistic_run_id: int,
) -> None:
    session.add(
        StudyResultRecord(
            job_id=job.id,
            linguistic_run_id=linguistic_run_id,
            content_language=CONTENT_LANGUAGE.value,
            study_language=job.study_language,
            dictionary_language=LOCAL_DICTIONARY_LANGUAGE.value,
        )
    )


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
