from __future__ import annotations

from typing import Any

import pytest

import mangasensei.runtime as runtime
from mangasensei.config import Settings


def test_blank_google_api_key_does_not_construct_gemini_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "")

    def unexpected_adapter(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("Gemini adapter must not be constructed for a blank key")

    monkeypatch.setattr(runtime, "GoogleGenAiAdapter", unexpected_adapter)

    assert runtime._gemini_adapter(Settings(_env_file=None)) is None


def test_configured_google_api_key_constructs_gemini_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "configured-test-key")
    captured: dict[str, str] = {}
    sentinel = object()

    def adapter_stub(*, model: str, api_key: str) -> object:
        captured["model"] = model
        captured["api_key"] = api_key
        return sentinel

    monkeypatch.setattr(runtime, "GoogleGenAiAdapter", adapter_stub)

    adapter = runtime._gemini_adapter(Settings(_env_file=None))

    assert adapter is sentinel
    assert captured == {
        "model": "gemini-3.6-flash",
        "api_key": "configured-test-key",
    }
