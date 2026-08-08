"""Read model for the protected study-page response."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.infrastructure.database.analysis_models import (
    GeminiAnalysisRecord,
    GeminiGrammarPointRecord,
    GeminiRegionAnalysisRecord,
    LinguisticMeaningRecord,
    LinguisticTokenRecord,
    OcrRegionRecord,
    OcrRegionVertexRecord,
    OcrRunRecord,
)
from mangasensei.infrastructure.database.job_models import JobRecord


class PageQueryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get(self, page_id: int) -> dict[str, Any]:
        async with self._sessions() as session:
            job = (
                await session.execute(
                    select(JobRecord)
                    .where(JobRecord.page_id == page_id)
                    .order_by(JobRecord.created_at.desc(), JobRecord.id.desc())
                    .limit(1)
                )
            ).scalar_one()
            run = (
                await session.execute(
                    select(OcrRunRecord)
                    .where(OcrRunRecord.job_id == job.id)
                    .order_by(OcrRunRecord.created_at.desc(), OcrRunRecord.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if run is None:
                return {"status": job.status, "regions": [], "error": _public_error(job)}

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
                            .where(LinguisticTokenRecord.region_id.in_(region_ids))
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
                        .where(GeminiAnalysisRecord.job_id == job.id)
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
            "status": job.status,
            "dimensions": {"width": run.width, "height": run.height},
            "ocr": {
                "detector": run.detector,
                "recognizer": run.recognizer,
                "upstreamCommit": run.upstream_commit,
            },
            "regions": response_regions,
            "error": _public_error(job),
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
