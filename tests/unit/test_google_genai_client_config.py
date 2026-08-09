from __future__ import annotations

from typing import Any

import pytest

import mangasensei.gemini.adapter as adapter_module
from mangasensei.gemini.adapter import GoogleGenAiAdapter


def test_real_google_client_disables_sdk_owned_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    sentinel = object()

    def client_stub(**kwargs: Any) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(adapter_module.genai, "Client", client_stub)

    adapter = GoogleGenAiAdapter(model="gemini-test", api_key="synthetic-key")

    assert adapter is not None
    http_options = captured["http_options"]
    assert http_options.retry_options is not None
    assert http_options.retry_options.attempts == 0
