from __future__ import annotations

from types import SimpleNamespace

import pytest

from mangasensei.gemini.adapter import GoogleGenAiAdapter
from mangasensei.gemini.contracts import GeminiPageAnalysis


class FakeInteractions:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> object:
        self.request = kwargs
        return SimpleNamespace(
            id="interaction-001",
            output_text=(
                '{"regions":[{"region_id":"region-001","translation":"É um gato.",'
                '"explanation":"Frase simples.","grammar_points":["です"],'
                '"vocabulary_ids":["jmdict-1467640"]}]}'
            ),
        )


@pytest.mark.asyncio
async def test_interactions_adapter_disables_storage_and_enforces_json_schema() -> None:
    interactions = FakeInteractions()
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    adapter = GoogleGenAiAdapter(client=client, model="gemini-test", timeout_seconds=15)

    result = await adapter.analyze(prompt="region-001", schema=GeminiPageAnalysis)

    assert result.regions[0].region_id == "region-001"
    assert interactions.request is not None
    assert interactions.request["store"] is False
    assert interactions.request["input"] == "region-001"
    assert interactions.request["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": GeminiPageAnalysis.model_json_schema(),
    }
    assert interactions.request["generation_config"] == {
        "thinking_level": "low",
        "max_output_tokens": 16_384,
    }


@pytest.mark.asyncio
async def test_interactions_adapter_accepts_a_bounded_output_limit_for_smoke_calls() -> None:
    interactions = FakeInteractions()
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    adapter = GoogleGenAiAdapter(
        client=client,
        model="gemini-test",
        max_output_tokens=128,
    )

    await adapter.analyze(prompt="region-001", schema=GeminiPageAnalysis)

    assert interactions.request is not None
    assert interactions.request["generation_config"] == {
        "thinking_level": "low",
        "max_output_tokens": 128,
    }


@pytest.mark.parametrize("max_output_tokens", [0, 65_537])
def test_interactions_adapter_rejects_invalid_output_limits(max_output_tokens: int) -> None:
    client = SimpleNamespace(aio=SimpleNamespace(interactions=FakeInteractions()))

    with pytest.raises(ValueError, match="output token limit"):
        GoogleGenAiAdapter(
            client=client,
            model="gemini-test",
            max_output_tokens=max_output_tokens,
        )
