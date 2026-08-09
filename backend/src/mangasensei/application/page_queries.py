"""Read model for the protected study-page response."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.domain.languages import CONTENT_LANGUAGE, LOCAL_DICTIONARY_LANGUAGE
from mangasensei.infrastructure.database.analysis_models import (
    GeminiAnalysisRecord,
    GeminiGrammarPointRecord,
    GeminiRegionAnalysisRecord,
    LinguisticMeaningRecord,
    LinguisticRunRecord,
    LinguisticTokenRecord,
    OcrRegionRecord,
    OcrRegionVertexRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.study_models import StudyResultRecord


class PageQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, page_id: int) -> dict[str, Any]:
        async with self._sessions() as session:
            latest_job = (
                await session.execute(
                    select(JobRecord)
                    .where(JobRecord.page_id == page_id)
                    .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                    .limit(1)
                )
            ).scalar_one()
            study_result = (
                await session.execute(
                    select(StudyResultRecord)
                    .join(JobRecord, JobRecord.id == StudyResultRecord.job_id)
                    .where(
                        JobRecord.page_id == page_id,
                        JobRecord.status == "completed",
                    )
                    .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if study_result is not None:
                linguistic_run = await session.get_one(
                    LinguisticRunRecord, study_result.linguistic_run_id
                )
                run = await session.get_one(OcrRunRecord, linguistic_run.ocr_run_id)
                result_job_id = study_result.job_id
                content_language = study_result.content_language
                study_language = study_result.study_language
                dictionary_language = study_result.dictionary_language
            else:
                # Defensive compatibility for records created by the pre-language schema.
                legacy_run = (
                    await session.execute(
                        select(OcrRunRecord)
                        .join(JobRecord, JobRecord.id == OcrRunRecord.job_id)
                        .where(
                            JobRecord.page_id == page_id,
                            JobRecord.status == "completed",
                        )
                        .order_by(
                            JobRecord.created_at.desc(),
                            JobRecord.id.desc(),
                            OcrRunRecord.fencing_token.desc(),
                            OcrRunRecord.id.desc(),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if legacy_run is None:
                    return {
                        "status": latest_job.status,
                        "resultAvailable": False,
                        "contentLanguage": CONTENT_LANGUAGE.value,
                        "studyLanguage": latest_job.study_language,
                        "dictionaryLanguage": LOCAL_DICTIONARY_LANGUAGE.value,
                        "regions": [],
                        "error": _public_error(latest_job),
                    }
                run = legacy_run
                linguistic_run = (
                    await session.execute(
                        select(LinguisticRunRecord)
                        .where(LinguisticRunRecord.ocr_run_id == run.id)
                        .order_by(
                            LinguisticRunRecord.fencing_token.desc(),
                            LinguisticRunRecord.id.desc(),
                        )
                        .limit(1)
                    )
                ).scalar_one()
                result_job = await session.get_one(JobRecord, run.job_id)
                result_job_id = run.job_id
                content_language = CONTENT_LANGUAGE.value
                study_language = result_job.study_language
                dictionary_language = LOCAL_DICTIONARY_LANGUAGE.value

            regions = tuple(
                (
                    await session.execute(
                        select(OcrRegionRecord)
                        .where(OcrRegionRecord.ocr_run_id == run.id)
                        .order_by(OcrRegionRecord.reading_order)
                    )
                ).scalars()
            )
            region_ids = tuple(region.id for region in regions)
            tokens = (
                tuple(
                    (
                        await session.execute(
                            select(LinguisticTokenRecord)
                            .where(
                                LinguisticTokenRecord.linguistic_run_id == linguistic_run.id,
                                LinguisticTokenRecord.region_id.in_(region_ids),
                            )
                            .order_by(
                                LinguisticTokenRecord.region_id,
                                LinguisticTokenRecord.token_ordinal,
                            )
                        )
                    ).scalars()
                )
                if region_ids
                else ()
            )
            token_ids = tuple(token.id for token in tokens)
            meanings = (
                tuple(
                    (
                        await session.execute(
                            select(LinguisticMeaningRecord)
                            .where(LinguisticMeaningRecord.token_id.in_(token_ids))
                            .order_by(
                                LinguisticMeaningRecord.token_id,
                                LinguisticMeaningRecord.meaning_ordinal,
                            )
                        )
                    ).scalars()
                )
                if token_ids
                else ()
            )
            vertices = (
                tuple(
                    (
                        await session.execute(
                            select(OcrRegionVertexRecord)
                            .where(OcrRegionVertexRecord.region_id.in_(region_ids))
                            .order_by(
                                OcrRegionVertexRecord.region_id,
                                OcrRegionVertexRecord.vertex_ordinal,
                            )
                        )
                    ).scalars()
                )
                if region_ids
                else ()
            )
            analyses = tuple(
                (
                    await session.execute(
                        select(GeminiRegionAnalysisRecord)
                        .join(
                            GeminiAnalysisRecord,
                            GeminiAnalysisRecord.id == GeminiRegionAnalysisRecord.analysis_id,
                        )
                        .where(GeminiAnalysisRecord.job_id == result_job_id)
                    )
                ).scalars()
            )
            analysis_ids = tuple(analysis.id for analysis in analyses)
            grammar = (
                tuple(
                    (
                        await session.execute(
                            select(GeminiGrammarPointRecord)
                            .where(GeminiGrammarPointRecord.region_analysis_id.in_(analysis_ids))
                            .order_by(
                                GeminiGrammarPointRecord.region_analysis_id,
                                GeminiGrammarPointRecord.grammar_ordinal,
                            )
                        )
                    ).scalars()
                )
                if analysis_ids
                else ()
            )

        meanings_by_token: dict[int, list[str]] = defaultdict(list)
        for meaning in meanings:
            meanings_by_token[meaning.token_id].append(meaning.meaning)
        tokens_by_region: dict[int, list[LinguisticTokenRecord]] = defaultdict(list)
        for token in tokens:
            tokens_by_region[token.region_id].append(token)
        vertices_by_region: dict[int, list[list[int]]] = defaultdict(list)
        for vertex in vertices:
            vertices_by_region[vertex.region_id].append([vertex.x, vertex.y])
        analysis_by_region = {analysis.region_id: analysis for analysis in analyses}
        grammar_by_analysis: dict[int, list[str]] = defaultdict(list)
        for point in grammar:
            grammar_by_analysis[point.region_analysis_id].append(point.label)

        response_regions = []
        for region in regions:
            analysis = analysis_by_region.get(region.id)
            region_tokens = tokens_by_region[region.id]
            vocabulary = []
            seen_entries: set[str] = set()
            for token in region_tokens:
                if (
                    token.dictionary_entry_id is None
                    or token.dictionary_entry_id in seen_entries
                ):
                    continue
                seen_entries.add(token.dictionary_entry_id)
                vocabulary.append(_vocabulary(token, meanings_by_token[token.id]))
            response_regions.append(
                {
                    "id": str(region.public_id),
                    "text": region.corrected_text or region.raw_text,
                    "rawText": region.raw_text,
                    "correctedText": region.corrected_text,
                    "bbox": {
                        "x": region.x,
                        "y": region.y,
                        "width": region.width,
                        "height": region.height,
                    },
                    "normalizedBbox": {
                        "x": float(region.normalized_x),
                        "y": float(region.normalized_y),
                        "width": float(region.normalized_width),
                        "height": float(region.normalized_height),
                    },
                    "polygon": vertices_by_region.get(region.id),
                    "angle": float(region.angle),
                    "confidence": float(region.confidence),
                    "readingOrder": region.reading_order,
                    "tokens": [_token(token) for token in region_tokens],
                    "translation": analysis.translation if analysis else None,
                    "explanation": analysis.explanation if analysis else None,
                    "grammar": grammar_by_analysis[analysis.id] if analysis else [],
                    "vocabulary": vocabulary,
                }
            )
        return {
            "status": latest_job.status,
            "resultAvailable": True,
            "contentLanguage": content_language,
            "studyLanguage": study_language,
            "dictionaryLanguage": dictionary_language,
            "dimensions": {"width": run.width, "height": run.height},
            "ocr": {
                "detector": run.detector,
                "recognizer": run.recognizer,
                "upstreamCommit": run.upstream_commit,
            },
            "regions": response_regions,
            "error": _public_error(latest_job),
        }


def _token(token: LinguisticTokenRecord) -> dict[str, Any]:
    return {
        "surface": token.surface,
        "lemma": token.lemma,
        "reading": token.reading,
        "partOfSpeech": token.part_of_speech,
        "dictionaryId": token.dictionary_entry_id,
    }


def _vocabulary(token: LinguisticTokenRecord, meanings: list[str]) -> dict[str, Any]:
    return {
        "id": token.dictionary_entry_id,
        "surface": token.surface,
        "lemma": token.lemma,
        "reading": token.reading,
        "meanings": meanings,
        "source": token.dictionary_source,
        "jlpt": (
            {"level": token.jlpt_level, "official": False} if token.jlpt_level is not None else None
        ),
    }


def _public_error(job: JobRecord) -> dict[str, str] | None:
    if job.status not in {"failed", "retryable_failure"}:
        return None
    return {
        "code": job.error_code or "processing_failed",
        "message": "Não foi possível concluir o processamento desta página.",
    }
