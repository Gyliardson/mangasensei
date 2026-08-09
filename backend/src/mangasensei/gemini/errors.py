"""Sanitized Gemini failure taxonomy shared by adapters and orchestration."""

from __future__ import annotations

from enum import StrEnum


class GeminiProviderFailureKind(StrEnum):
    """Stable internal categories for provider failures without provider payloads."""

    REQUEST = "request"
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    SERVER = "server"
    TRANSPORT = "transport"
    UNKNOWN = "unknown"


class GeminiProviderError(RuntimeError):
    """A sanitized provider failure safe for internal orchestration."""

    def __init__(
        self,
        *,
        kind: GeminiProviderFailureKind,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__("Gemini request failed")
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code


class GeminiResponseError(RuntimeError):
    """The provider returned output outside the strict local response contract."""
