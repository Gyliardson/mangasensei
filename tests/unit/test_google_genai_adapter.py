from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from mangasensei.gemini.adapter import (
    GeminiProviderError,
    GeminiProviderFailureKind,
    GoogleGenAiAdapter,
)
from mangasensei.gemini.contracts import GeminiPageAnalysis


class FakeInteractions:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None
        self.calls = 0

    async def create(self, **kwargs: object) -> object:
        self.calls += 1
        self.request = kwargs
        return SimpleNamespace(
            id="interaction-001",
            output_text=(
                '{"regions":[{"region_id":"region-001","translation":"É um gato.",'
                '"explanation":"Frase simples.","grammar_points":["です"],'
                '"vocabulary_ids":["jmdict-1467640"]}]}'
            ),
        )


class SyntheticProviderError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__("synthetic provider detail must not cross the adapter boundary")
        self.status_code = status_code


class FakeProviderFailureInteractions:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    async def create(self, **kwargs: object) -> object:
        del kwargs
        self.calls += 1
        raise SyntheticProviderError(self.status_code)


@pytest.mark.asyncio
async def test_interactions_adapter_disables_storage_and_uses_supported_json_schema() -> None:
    interactions = FakeInteractions()
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    adapter = GoogleGenAiAdapter(client=client, model="gemini-test", timeout_seconds=15)

    result = await adapter.analyze(prompt="region-001", schema=GeminiPageAnalysis)

    assert result.regions[0].region_id == "region-001"
    assert interactions.request is not None
    assert interactions.request["store"] is False
    assert interactions.request["input"] == "region-001"
    response_format = interactions.request["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "text"
    assert response_format["mime_type"] == "application/json"
    provider_schema = response_format["schema"]
    serialized_provider_schema = json.dumps(provider_schema, sort_keys=True)
    assert "minLength" not in serialized_provider_schema
    assert "maxLength" not in serialized_provider_schema
    assert "maxItems" in serialized_provider_schema
    assert "additionalProperties" in serialized_provider_schema

    raw_schema = json.dumps(GeminiPageAnalysis.model_json_schema(), sort_keys=True)
    assert "minLength" in raw_schema
    assert "maxLength" in raw_schema
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


@pytest.mark.asyncio
async def test_interactions_adapter_default_keeps_one_http_attempt_per_accounting_call() -> None:
    interactions = FakeProviderFailureInteractions(503)
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    adapter = GoogleGenAiAdapter(client=client, model="gemini-test")

    with pytest.raises(GeminiProviderError) as captured:
        await adapter.analyze(prompt="synthetic", schema=GeminiPageAnalysis)

    assert interactions.calls == 1
    assert captured.value.kind is GeminiProviderFailureKind.SERVER
    assert captured.value.retryable is True
    assert captured.value.status_code == 503


@pytest.mark.asyncio
async def test_interactions_adapter_preserves_permanent_400_classification() -> None:
    interactions = FakeProviderFailureInteractions(400)
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    adapter = GoogleGenAiAdapter(client=client, model="gemini-test", max_attempts=3)

    with pytest.raises(GeminiProviderError) as captured:
        await adapter.analyze(prompt="synthetic", schema=GeminiPageAnalysis)

    assert interactions.calls == 1
    assert captured.value.kind is GeminiProviderFailureKind.REQUEST
    assert captured.value.retryable is False
    assert captured.value.status_code == 400
    assert str(captured.value) == "Gemini request failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "kind"),
    [
        (429, GeminiProviderFailureKind.RATE_LIMIT),
        (503, GeminiProviderFailureKind.SERVER),
    ],
)
async def test_interactions_adapter_can_retry_transient_provider_statuses_when_explicitly_bounded(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    kind: GeminiProviderFailureKind,
) -> None:
    interactions = FakeProviderFailureInteractions(status_code)
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    adapter = GoogleGenAiAdapter(client=client, model="gemini-test", max_attempts=2)

    async def no_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr("mangasensei.gemini.adapter.asyncio.sleep", no_sleep)

    with pytest.raises(GeminiProviderError) as captured:
        await adapter.analyze(prompt="synthetic", schema=GeminiPageAnalysis)

    assert interactions.calls == 2
    assert captured.value.kind is kind
    assert captured.value.retryable is True
    assert captured.value.status_code == status_code


@pytest.mark.asyncio
async def test_interactions_adapter_classifies_transport_cause_as_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WrappedTimeoutInteractions:
        def __init__(self) -> None:
            self.calls = 0

        async def create(self, **kwargs: Any) -> object:
            del kwargs
            self.calls += 1
            try:
                raise TimeoutError("synthetic timeout")
            except TimeoutError as cause:
                raise RuntimeError("synthetic wrapper") from cause

    interactions = WrappedTimeoutInteractions()
    client = SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    adapter = GoogleGenAiAdapter(client=client, model="gemini-test", max_attempts=2)

    async def no_sleep(delay: float) -> None:
        del delay

    monkeypatch.setattr("mangasensei.gemini.adapter.asyncio.sleep", no_sleep)

    with pytest.raises(GeminiProviderError) as captured:
        await adapter.analyze(prompt="synthetic", schema=GeminiPageAnalysis)

    assert interactions.calls == 2
    assert captured.value.kind is GeminiProviderFailureKind.TRANSPORT
    assert captured.value.retryable is True


@pytest.mark.parametrize("max_output_tokens", [0, 65_537])
def test_interactions_adapter_rejects_invalid_output_limits(max_output_tokens: int) -> None:
    client = SimpleNamespace(aio=SimpleNamespace(interactions=FakeInteractions()))

    with pytest.raises(ValueError, match="output token limit"):
        GoogleGenAiAdapter(
            client=client,
            model="gemini-test",
            max_output_tokens=max_output_tokens,
        )
