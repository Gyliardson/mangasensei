"""Dictionary-only worker path over persisted canonical linguistic analysis."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.domain.languages import FALLBACK_DICTIONARY_LANGUAGE, StudyLanguage
from mangasensei.gemini.service import GeminiAdapter
from mangasensei.infrastructure.database.dictionary_projection_models import (
    DictionaryProjectionItemRecord,
    DictionaryProjectionMeaningRecord,
    DictionaryProjectionRecord,
    DictionaryProjectionRequestRecord,
    DictionaryProjectionSourceRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.lexical_models import LexicalMatchRecord
from mangasensei.infrastructure.database.queue_repository import ClaimedJob
from mangasensei.infrastructure.database.study_models import StudyResultRecord
from mangasensei.linguistics.jmdict_glosses import (
    JmdictGlossSourceReference,
    LocalizedJmdictGloss,
    LocalizedJmdictGlossResolver,
)
from mangasensei.linguistics.service import LexicalFormIdentity, LinguisticService
from mangasensei.ocr.contracts import OcrEngine
from mangasensei.storage.local import LocalFilesystemStorage
from mangasensei.workers.runner import (
    Worker,
    _finish_attempt,
    _finish_job,
    _lock_owned_job,
)


class DictionaryProjectionWorker(Worker):
    """Extend the fenced worker with a zero-OCR dictionary projection job."""

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
        gloss_resolver: LocalizedJmdictGlossResolver,
        gemini_model: str = "fake-or-configured",
        gemini_daily_budget: Decimal = Decimal("5.00"),
        gemini_reservation: Decimal = Decimal("0.52"),
        gemini_max_calls_per_page: int = 3,
    ) -> None:
        super().__init__(
            sessions=sessions,
            storage=storage,
            ocr=ocr,
            linguistics=linguistics,
            gemini=gemini,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            gemini_model=gemini_model,
            gemini_daily_budget=gemini_daily_budget,
            gemini_reservation=gemini_reservation,
            gemini_max_calls_per_page=gemini_max_calls_per_page,
        )
        self._gloss_resolver = gloss_resolver

    async def _load_job_contract(self, claim: ClaimedJob) -> tuple[str, StudyLanguage]:
        job_kind, study_language = await super()._load_job_contract(claim)
        if job_kind == "dictionary_language_reprocess":
            # Reuse the base worker's pre-OCR dispatch point; this path never reaches OCR.
            return "study_language_reprocess", study_language
        return job_kind, study_language

    async def _run_study_language_reprocess(
        self,
        claim: ClaimedJob,
        study_language: StudyLanguage,
    ) -> None:
        async with self._sessions() as session:
            job_kind = await session.scalar(
                select(JobRecord.job_kind).where(JobRecord.id == claim.job_id)
            )
        if job_kind == "dictionary_language_reprocess":
            await self._run_dictionary_projection(claim)
            return
        await super()._run_study_language_reprocess(claim, study_language)

    async def _run_dictionary_projection(self, claim: ClaimedJob) -> None:
        async with self._sessions() as session:
            request = await session.get(DictionaryProjectionRequestRecord, claim.job_id)
            if request is None:
                raise ValueError("dictionary projection job is missing its durable request")
            latest_result = (
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
            if latest_result is None:
                raise ValueError("dictionary projection requires a completed study result")
            lexical_matches = tuple(
                (
                    await session.execute(
                        select(LexicalMatchRecord)
                        .where(
                            LexicalMatchRecord.linguistic_run_id
                            == latest_result.linguistic_run_id
                        )
                        .order_by(
                            LexicalMatchRecord.region_id,
                            LexicalMatchRecord.start_token_ordinal,
                            LexicalMatchRecord.end_token_ordinal,
                            LexicalMatchRecord.id,
                        )
                    )
                ).scalars()
            )
            requested_dictionary_language = request.requested_dictionary_language
            linguistic_run_id = latest_result.linguistic_run_id

        localized = tuple(
            (
                match,
                self._gloss_resolver.resolve(
                    LexicalFormIdentity(
                        dictionary_namespace=match.dictionary_namespace,
                        entry_id=match.dictionary_entry_id,
                        lemma=match.form_lemma,
                        reading=match.form_reading,
                    ),
                    requested_dictionary_language=requested_dictionary_language,
                ),
            )
            for match in lexical_matches
        )
        _assert_projection_identity(localized)

        async with self._sessions.begin() as session:
            job = await _lock_owned_job(session, claim, "claimed")
            durable_request = await session.get(DictionaryProjectionRequestRecord, job.id)
            if (
                durable_request is None
                or durable_request.requested_dictionary_language
                != requested_dictionary_language
            ):
                raise ValueError("dictionary projection request changed while processing")

            projection = DictionaryProjectionRecord(
                job_id=job.id,
                linguistic_run_id=linguistic_run_id,
                requested_dictionary_language=requested_dictionary_language,
                fallback_dictionary_language=FALLBACK_DICTIONARY_LANGUAGE.value,
            )
            session.add(projection)
            await session.flush()

            sources = _unique_sources(localized)
            session.add_all(
                [
                    DictionaryProjectionSourceRecord(
                        projection_job_id=job.id,
                        source_ref=source.compact_ref,
                        dataset=source.dataset,
                        product_language=source.language,
                        source_version=source.version,
                        normalized_digest=bytes.fromhex(source.digest_sha256),
                    )
                    for source in sources
                ]
            )
            await session.flush()

            for match, gloss in localized:
                session.add(
                    DictionaryProjectionItemRecord(
                        projection_job_id=job.id,
                        lexical_match_id=match.id,
                        effective_dictionary_language=gloss.effective_dictionary_language,
                        fallback_used=gloss.fallback_used,
                        fallback_reason=(
                            gloss.fallback_reason.value
                            if gloss.fallback_reason is not None
                            else None
                        ),
                        source_ref=gloss.source.compact_ref,
                    )
                )
            await session.flush()
            session.add_all(
                [
                    DictionaryProjectionMeaningRecord(
                        projection_job_id=job.id,
                        lexical_match_id=match.id,
                        meaning_ordinal=ordinal,
                        meaning=meaning,
                    )
                    for match, gloss in localized
                    for ordinal, meaning in enumerate(gloss.meanings)
                ]
            )
            job.status = "completed"
            _finish_job(job)
            await _finish_attempt(session, claim, "completed_dictionary_projection")


def _assert_projection_identity(
    localized: tuple[tuple[LexicalMatchRecord, LocalizedJmdictGloss], ...],
) -> None:
    for match, gloss in localized:
        identity = LexicalFormIdentity(
            dictionary_namespace=match.dictionary_namespace,
            entry_id=match.dictionary_entry_id,
            lemma=match.form_lemma,
            reading=match.form_reading,
        )
        if gloss.identity != identity:
            raise ValueError("localized dictionary projection changed canonical lexical identity")


def _unique_sources(
    localized: tuple[tuple[LexicalMatchRecord, LocalizedJmdictGloss], ...],
) -> tuple[JmdictGlossSourceReference, ...]:
    by_ref: dict[str, JmdictGlossSourceReference] = {}
    for _, gloss in localized:
        existing = by_ref.setdefault(gloss.source.compact_ref, gloss.source)
        if existing != gloss.source:
            raise ValueError("dictionary source reference collision")
    return tuple(by_ref[key] for key in sorted(by_ref))
