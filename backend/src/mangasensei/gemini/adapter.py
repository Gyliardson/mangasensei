"""Current google-genai Interactions API adapter."""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar, cast

import httpx
from google import genai
from pydantic import BaseModel, ValidationError

from mangasensei.gemini.errors import (
    GeminiProviderError,
    GeminiProviderFailureKind,
    GeminiResponseError,
)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

_UNSUPPORTED_INTERACTIONS_SCHEMA_KEYS = frozenset({"minLength", "maxLength"})
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class GoogleGenAiAdapter:
    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float = 60,
        max_attempts: int = 2,
        max_output_tokens: int = 16_384,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None and not api_key:
            raise ValueError("a Gemini API key or injected client is required")
        if not 1 <= max_attempts <= 3:
            raise ValueError("Gemini attempts must be between 1 and 3")
        if not 1 <= max_output_tokens <= 65_536:
            raise ValueError("Gemini output token limit must be between 1 and 65536")
        self._client = client or genai.Client(api_key=api_key)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._max_output_tokens = max_output_tokens

    async def analyze(self, *, prompt: str, schema: type[SchemaT]) -> SchemaT:
        response: Any | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._client.aio.interactions.create(
                    model=self._model,
                    input=prompt,
                    store=False,
                    response_format={
                        "type": "text",
                        "mime_type": "application/json",
                        "schema": _interactions_json_schema(schema),
                    },
                    generation_config={
                        "thinking_level": "low",
                        "max_output_tokens": self._max_output_tokens,
                    },
                    timeout=self._timeout_seconds,
                )
                break
            except Exception as exc:
                failure = _classify_provider_failure(exc)
                if attempt == self._max_attempts or not failure.retryable:
                    raise failure from exc
                await asyncio.sleep(2 ** (attempt - 1))

        output_text = getattr(response, "output_text", None)
        if not isinstance(output_text, str) or not output_text:
            raise GeminiResponseError("Gemini returned no structured text")
        try:
            return schema.model_validate_json(output_text)
        except ValidationError as exc:
            raise GeminiResponseError("Gemini output failed schema validation") from exc

    async def close(self) -> None:
        aio = getattr(self._client, "aio", None)
        close = getattr(aio, "aclose", None)
        if close is not None:
            await close()


def _interactions_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Return the documented Gemini JSON-Schema subset without weakening local validation."""
    return cast(dict[str, Any], _strip_unsupported_schema_keys(schema.model_json_schema()))


def _strip_unsupported_schema_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_unsupported_schema_keys(item)
            for key, item in value.items()
            if key not in _UNSUPPORTED_INTERACTIONS_SCHEMA_KEYS
        }
    if isinstance(value, list):
        return [_strip_unsupported_schema_keys(item) for item in value]
    return value


def _classify_provider_failure(exc: Exception) -> GeminiProviderError:
    status_code = _provider_status_code(exc)
    if _contains_transport_failure(exc):
        return GeminiProviderError(
            kind=GeminiProviderFailureKind.TRANSPORT,
            retryable=True,
            status_code=status_code,
        )
    if status_code in {401, 403}:
        return GeminiProviderError(
            kind=GeminiProviderFailureKind.AUTH,
            retryable=False,
            status_code=status_code,
        )
    if status_code == 429:
        return GeminiProviderError(
            kind=GeminiProviderFailureKind.RATE_LIMIT,
            retryable=True,
            status_code=status_code,
        )
    if status_code in {408, 500, 502, 503, 504}:
        kind = (
            GeminiProviderFailureKind.TRANSPORT
            if status_code == 408
            else GeminiProviderFailureKind.SERVER
        )
        return GeminiProviderError(kind=kind, retryable=True, status_code=status_code)
    if status_code is not None and 400 <= status_code < 500:
        return GeminiProviderError(
            kind=GeminiProviderFailureKind.REQUEST,
            retryable=False,
            status_code=status_code,
        )
    return GeminiProviderError(
        kind=GeminiProviderFailureKind.UNKNOWN,
        retryable=status_code in _RETRYABLE_HTTP_STATUSES,
        status_code=status_code,
    )


def _provider_status_code(exc: BaseException) -> int | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attribute in ("status_code", "code"):
            value = getattr(current, attribute, None)
            if isinstance(value, int):
                return value
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return None


def _contains_transport_failure(exc: BaseException) -> bool:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, httpx.TransportError)):
            return True
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return False


__all__ = [
    "GeminiProviderError",
    "GeminiProviderFailureKind",
    "GeminiResponseError",
    "GoogleGenAiAdapter",
]
