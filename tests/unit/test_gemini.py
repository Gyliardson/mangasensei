import json

import pytest
from pydantic import ValidationError

from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.gemini.service import (
    GeminiAnalysisService,
    GeminiVocabularyCandidate,
    RegionCompletenessError,
    UnknownRegionError,
    UnknownVocabularyError,
    build_vocabulary_candidates_by_region,
)
from mangasensei.linguistics.service import LinguisticToken


class PromptMappedAdapter:
    def __init__(self) -> None:
        self.payload: dict[str, object] | None = None

    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        payload = json.loads(prompt)
        self.payload = payload
        return schema(
            regions=tuple(
                GeminiRegionAnalysis(
                    region_id=region["region_id"],
                    translation=f"translation:{region['region_id']}",
                    explanation=f"explanation:{region['region_id']}",
                    grammar_points=(),
                    vocabulary_ids=tuple(
                        candidate["id"] for candidate in region["vocabulary_candidates"]
                    ),
                )
                for region in payload["regions"]
            )
        )


def candidate(id_: str, surface: str, lemma: str, reading: str) -> GeminiVocabularyCandidate:
    return GeminiVocabularyCandidate(id=id_, surface=surface, lemma=lemma, reading=reading)


def token(
    *, region_id: str, index: int, id_: str | None, surface: str, lemma: str, reading: str
) -> LinguisticToken:
    return LinguisticToken(
        id=f"{region_id}:token:{index}",
        surface=surface,
        lemma=lemma,
        reading=reading,
        part_of_speech="名詞",
        dictionary_id=id_,
        meanings=("not sent to Gemini",) if id_ is not None else (),
        source="fixture" if id_ is not None else None,
        jlpt_level="N5" if id_ is not None else None,
        jlpt_official=False if id_ is not None else None,
    )


def region_analysis(region_id: str, *, vocabulary_ids: tuple[str, ...] = ()) -> GeminiRegionAnalysis:
    return GeminiRegionAnalysis(
        region_id=region_id,
        translation=f"translation:{region_id}",
        explanation=f"explanation:{region_id}",
        grammar_points=(),
        vocabulary_ids=vocabulary_ids,
    )


@pytest.mark.asyncio
async def test_prompt_preserves_region_scoped_vocabulary_mapping() -> None:
    adapter = PromptMappedAdapter()
    service = GeminiAnalysisService(adapter, prompt_version="v2")
    vocabulary = {
        "region-a": (candidate("jmdict-cat", "猫", "猫", "ネコ"),),
        "region-b": (candidate("jmdict-dog", "犬", "犬", "イヌ"),),
    }

    result = await service.analyze_page(
        regions={"region-a": "猫です", "region-b": "犬です"},
        vocabulary_by_region=vocabulary,
    )

    assert [region.vocabulary_ids for region in result.regions] == [
        ("jmdict-cat",),
        ("jmdict-dog",),
    ]
    assert adapter.payload is not None
    prompt_regions = adapter.payload["regions"]
    assert prompt_regions == [
        {
            "region_id": "region-a",
            "japanese_text": "猫です",
            "vocabulary_candidates": [
                {"id": "jmdict-cat", "surface": "猫", "lemma": "猫", "reading": "ネコ"}
            ],
        },
        {
            "region_id": "region-b",
            "japanese_text": "犬です",
            "vocabulary_candidates": [
                {"id": "jmdict-dog", "surface": "犬", "lemma": "犬", "reading": "イヌ"}
            ],
        },
    ]


@pytest.mark.asyncio
async def test_known_page_vocabulary_is_rejected_for_the_wrong_region() -> None:
    class WrongRegionVocabularyAdapter:
        async def analyze(
            self, *, prompt: str, schema: type[GeminiPageAnalysis]
        ) -> GeminiPageAnalysis:
            del prompt
            return schema(
                regions=(
                    region_analysis("region-a", vocabulary_ids=("jmdict-cat",)),
                    region_analysis("region-b", vocabulary_ids=("jmdict-cat",)),
                )
            )

    service = GeminiAnalysisService(WrongRegionVocabularyAdapter(), prompt_version="v2")
    with pytest.raises(UnknownVocabularyError, match="jmdict-cat"):
        await service.analyze_page(
            regions={"region-a": "猫", "region-b": "犬"},
            vocabulary_by_region={
                "region-a": (candidate("jmdict-cat", "猫", "猫", "ネコ"),),
                "region-b": (candidate("jmdict-dog", "犬", "犬", "イヌ"),),
            },
        )


@pytest.mark.asyncio
async def test_missing_gemini_region_is_rejected_before_persistence() -> None:
    class MissingRegionAdapter:
        async def analyze(
            self, *, prompt: str, schema: type[GeminiPageAnalysis]
        ) -> GeminiPageAnalysis:
            del prompt
            return schema(regions=(region_analysis("region-a"),))

    service = GeminiAnalysisService(MissingRegionAdapter(), prompt_version="v2")
    with pytest.raises(RegionCompletenessError):
        await service.analyze_page(
            regions={"region-a": "猫", "region-b": "犬"},
            vocabulary_by_region={"region-a": (), "region-b": ()},
        )


@pytest.mark.asyncio
async def test_duplicate_gemini_region_is_rejected_before_persistence() -> None:
    class DuplicateRegionAdapter:
        async def analyze(
            self, *, prompt: str, schema: type[GeminiPageAnalysis]
        ) -> GeminiPageAnalysis:
            del prompt
            return schema(
                regions=(region_analysis("region-a"), region_analysis("region-a"))
            )

    service = GeminiAnalysisService(DuplicateRegionAdapter(), prompt_version="v2")
    with pytest.raises(RegionCompletenessError):
        await service.analyze_page(
            regions={"region-a": "猫", "region-b": "犬"},
            vocabulary_by_region={"region-a": (), "region-b": ()},
        )


@pytest.mark.asyncio
async def test_complete_gemini_regions_can_be_returned_in_any_order() -> None:
    class ReorderedRegionAdapter:
        async def analyze(
            self, *, prompt: str, schema: type[GeminiPageAnalysis]
        ) -> GeminiPageAnalysis:
            del prompt
            return schema(
                regions=(region_analysis("region-b"), region_analysis("region-a"))
            )

    result = await GeminiAnalysisService(
        ReorderedRegionAdapter(), prompt_version="v2"
    ).analyze_page(
        regions={"region-a": "猫", "region-b": "犬"},
        vocabulary_by_region={"region-a": (), "region-b": ()},
    )

    assert [analysis.region_id for analysis in result.regions] == ["region-b", "region-a"]


def test_vocabulary_candidates_dedupe_within_region_but_remain_region_scoped() -> None:
    candidates = build_vocabulary_candidates_by_region(
        {
            "region-a": (
                token(
                    region_id="region-a",
                    index=0,
                    id_="jmdict-cat",
                    surface="猫",
                    lemma="猫",
                    reading="ネコ",
                ),
                token(
                    region_id="region-a",
                    index=1,
                    id_="jmdict-cat",
                    surface="猫",
                    lemma="猫",
                    reading="ネコ",
                ),
            ),
            "region-b": (
                token(
                    region_id="region-b",
                    index=0,
                    id_="jmdict-cat",
                    surface="猫",
                    lemma="猫",
                    reading="ネコ",
                ),
                token(
                    region_id="region-b",
                    index=1,
                    id_=None,
                    surface="です",
                    lemma="です",
                    reading="デス",
                ),
            ),
        }
    )

    assert candidates == {
        "region-a": (candidate("jmdict-cat", "猫", "猫", "ネコ"),),
        "region-b": (candidate("jmdict-cat", "猫", "猫", "ネコ"),),
    }


def test_gemini_contract_is_strict() -> None:
    with pytest.raises(ValidationError):
        GeminiRegionAnalysis.model_validate(
            {
                "region_id": "region-001",
                "translation": "Cat.",
                "explanation": "Simple sentence.",
                "grammar_points": [],
                "vocabulary_ids": [],
                "unexpected": "not accepted",
            }
        )


@pytest.mark.asyncio
async def test_unknown_gemini_region_is_rejected() -> None:
    class UnknownAdapter:
        async def analyze(
            self, *, prompt: str, schema: type[GeminiPageAnalysis]
        ) -> GeminiPageAnalysis:
            del prompt
            return schema(regions=(region_analysis("region-999"),))

    service = GeminiAnalysisService(UnknownAdapter(), prompt_version="v2")
    with pytest.raises(UnknownRegionError):
        await service.analyze_page(
            regions={"region-001": "猫"},
            vocabulary_by_region={"region-001": ()},
        )
