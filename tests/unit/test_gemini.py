import pytest
from pydantic import ValidationError

from mangasensei.gemini.contracts import GeminiPageAnalysis, GeminiRegionAnalysis
from mangasensei.gemini.service import GeminiAnalysisService, UnknownRegionError


class FakeGeminiAdapter:
    async def analyze(self, *, prompt: str, schema: type[GeminiPageAnalysis]) -> GeminiPageAnalysis:
        assert "region-001" in prompt
        return schema(
            regions=(
                GeminiRegionAnalysis(
                    region_id="region-001",
                    translation="É um gato.",
                    explanation="Frase copulativa simples.",
                    grammar_points=("です",),
                    vocabulary_ids=("jmdict-1467640",),
                ),
            )
        )


@pytest.mark.asyncio
async def test_gemini_can_only_associate_known_region_and_vocabulary_ids() -> None:
    service = GeminiAnalysisService(FakeGeminiAdapter(), prompt_version="v1")

    result = await service.analyze_page(
        regions={"region-001": "猫です"},
        vocabulary_ids=frozenset({"jmdict-1467640"}),
    )

    assert result.regions[0].translation == "É um gato."


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
    class UnknownAdapter(FakeGeminiAdapter):
        async def analyze(
            self, *, prompt: str, schema: type[GeminiPageAnalysis]
        ) -> GeminiPageAnalysis:
            return schema(
                regions=(
                    GeminiRegionAnalysis(
                        region_id="region-999",
                        translation="?",
                        explanation="?",
                        grammar_points=(),
                        vocabulary_ids=(),
                    ),
                )
            )

    service = GeminiAnalysisService(UnknownAdapter(), prompt_version="v1")
    with pytest.raises(UnknownRegionError):
        await service.analyze_page(regions={"region-001": "猫"}, vocabulary_ids=frozenset())
