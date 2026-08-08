from __future__ import annotations

from typing import Any

import pytest

import mangasensei.runtime as runtime
from mangasensei.config import Settings


@pytest.mark.parametrize("environment_value", [None, ""])
def test_missing_or_blank_google_api_key_does_not_construct_gemini_adapter(
    monkeypatch: pytest.MonkeyPatch,
    environment_value: str | None,
) -> None:
    if environment_value is None:
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    else:
        monkeypatch.setenv("GOOGLE_API_KEY", environment_value)

    def unexpected_adapter(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("Gemini adapter must not be constructed without a configured key")

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


@pytest.mark.asyncio
async def test_retention_process_does_not_require_capability_peppers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://u:p@host/db",
        capability_peppers=None,
    )
    sessions = object()
    janitor = object()
    captured: dict[str, object] = {}

    class EngineStub:
        disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engine = EngineStub()

    def database_stub(database_url: str) -> tuple[EngineStub, object]:
        captured["database_url"] = database_url
        return engine, sessions

    def janitor_stub(sessions_arg: object, storage: object) -> object:
        captured["sessions"] = sessions_arg
        captured["storage"] = storage
        return janitor

    async def loop_stub(
        janitor_arg: object, *, poll_seconds: float, once: bool = False
    ) -> None:
        captured["janitor"] = janitor_arg
        captured["poll_seconds"] = poll_seconds
        captured["once"] = once

    monkeypatch.setattr(runtime, "create_database", database_stub)
    monkeypatch.setattr(runtime, "RetentionJanitor", janitor_stub)
    monkeypatch.setattr(runtime, "run_retention_loop", loop_stub)

    await runtime.run_retention_process(settings, once=True)

    assert captured["database_url"] == "postgresql+psycopg://u:p@host/db"
    assert captured["sessions"] is sessions
    assert captured["janitor"] is janitor
    assert captured["poll_seconds"] == settings.retention_poll_seconds
    assert captured["once"] is True
    assert engine.disposed is True
