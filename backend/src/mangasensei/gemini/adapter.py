"""Current google-genai Interactions API adapter."""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

import httpx
from google import genai
from pydantic import BaseModel, ValidationError

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class GeminiProviderError(RuntimeError):
    """A sanitized provider failure safe for internal orchestration."""


class GeminiResponseError(RuntimeError):
    """The provider returned output outside the strict schema."""


class GoogleGenAiAdapter:
    def __init__(
        self,
        *,
        model: str,
        timeout_seconds: float = 60,
        max_attempts: int = 2,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        if client is None and not api_key:
            raise ValueError("a Gemini API key or injected client is required")
        if not 1 <= max_attempts <= 3:
            raise ValueError("Gemini attempts must be between 1 and 3")
        self._client = client or genai.Client(api_key=api_key)
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts

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
                        "schema": schema.model_json_schema(),
                    },
                    generation_config={
                        "thinking_level": "low",
                        "max_output_tokens": 16_384,
                    },
                    timeout=self._timeout_seconds,
                )
                break
            except Exception as exc:
                if attempt == self._max_attempts or not _is_retryable(exc):
                    raise GeminiProviderError("Gemini request failed") from exc
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


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TransportError)):
        return True
    status_code = getattr(exc, "status_code", getattr(exc, "code", None))
    return status_code in {408, 429, 500, 502, 503, 504}
