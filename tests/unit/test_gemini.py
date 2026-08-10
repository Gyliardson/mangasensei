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
from mangasensei.linguistics.service import (
    LexicalFormIdentity,
    LexicalMatch,
    LinguisticAnalysis,
)


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


def lexical_match(
    *, region_id: str, index: int, entry_id: str, surface: str, lemma: str, reading: str
) -> LexicalMatch:
    return LexicalMatch(
        id=f"{region_id}:lexical:{index}",
        start_token_ordinal=index,
        end_token_ordinal=index + 1,
        surface=surface,
        display_lemma=lemma,
        display_reading=reading,
        identity=LexicalFormIdentity(
            dictionary_namespace="JMdict",
            entry_id=entry_id,
            lemma=lemma,
            reading=reading,
        ),
        meanings=("not sent to Gemini",),
        source="fixture",
        jlpt_level="N5",
        jlpt_official=False,
    )


def region_analysis(
    region_id: str, *, vocabulary_ids: tuple[str, ...] = ()
) -> GeminiRegionAnalysis:
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
        "region-a": (candidate("lexical-cat", "猫", "猫", "ネコ"),),
        "region-b": (candidate("lexical-dog", "犬", "犬", "イヌ"),),
    }

    result = await service.analyze_page(
        regions={"region-a": "猫です", "region-b": "犬です"},
        vocabulary_by_region=vocabulary,
    )

    assert [region.vocabulary_ids for region in result.regions] == [
        ("lexical-cat",),
        ("lexical-dog",),
    ]
    assert adapter.payload is not None
    prompt_regions = adapter.payload["regions"]
    assert prompt_regions == [
        {
            "region_id": "region-a",
            "japanese_text": "猫です",
            "vocabulary_candidates": [
                {"id": "lexical-cat", "surface": "猫", "lemma": "猫", "reading": "ネコ"}
            ],
        },
        {
            "region_id": "region-b",
            "japanese_text": "犬です",
            "vocabulary_candidates": [
                {"id": "lexical-dog", "surface": "犬", "lemma": "犬", "reading": "イヌ"}
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
                    region_analysis("region-a", vocabulary_ids=("lexical-cat",)),
                    region_analysis("region-b", vocabulary_ids=("lexical-cat",)),
                )
            )

    service = GeminiAnalysisService(WrongRegionVocabularyAdapter(), prompt_version="v2")
    with pytest.raises(UnknownVocabularyError, match="lexical-cat"):
        await service.analyze_page(
            regions={"region-a": "猫", "region-b": "犬"},
            vocabulary_by_region={
                "region-a": (candidate("lexical-cat", "猫", "猫", "ネコ"),),
                "region-b": (candidate("lexical-dog", "犬", "犬", "イヌ"),),
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


def test_vocabulary_candidates_dedupe_by_canonical_identity_within_region() -> None:
    first = lexical_match(
        region_id="region-a",
        index=0,
        entry_id="jmdict-cat",
        surface="猫",
        lemma="猫",
        reading="ねこ",
    )
    repeated = lexical_match(
        region_id="region-a",
        index=1,
        entry_id="jmdict-cat",
        surface="猫",
        lemma="猫",
        reading="ねこ",
    )
    other_region = lexical_match(
        region_id="region-b",
        index=0,
        entry_id="jmdict-cat",
        surface="猫",
        lemma="猫",
        reading="ねこ",
    )
    candidates = build_vocabulary_candidates_by_region(
        {
            "region-a": LinguisticAnalysis(tokens=(), lexical_matches=(first, repeated)),
            "region-b": LinguisticAnalysis(tokens=(), lexical_matches=(other_region,)),
        }
    )

    expected_id = first.identity.transport_id
    assert candidates == {
        "region-a": (candidate(expected_id, "猫", "猫", "ねこ"),),
        "region-b": (candidate(expected_id, "猫", "猫", "ねこ"),),
    }


def test_distinct_forms_of_same_entry_remain_distinct_gemini_candidates() -> None:
    first = lexical_match(
        region_id="region-a",
        index=0,
        entry_id="jmdict-shared",
        surface="表記一",
        lemma="表記一",
        reading="よみ",
    )
    second = lexical_match(
        region_id="region-a",
        index=1,
        entry_id="jmdict-shared",
        surface="表記二",
        lemma="表記二",
        reading="よみ",
    )

    candidates = build_vocabulary_candidates_by_region(
        {"region-a": LinguisticAnalysis(tokens=(), lexical_matches=(first, second))}
    )["region-a"]

    assert len(candidates) == 2
    assert candidates[0].id != candidates[1].id


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
