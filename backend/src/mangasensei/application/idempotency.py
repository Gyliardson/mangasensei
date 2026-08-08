"""Shared validation and one-way persistence for idempotency keys."""

from __future__ import annotations

import hashlib
import hmac
import re

_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._~:+/=-]{16,128}$")


class InvalidIdempotencyKeyError(ValueError):
    """The caller supplied an idempotency key outside the public contract."""


def idempotency_digest(*, pepper: bytes, namespace: str, value: str) -> bytes:
    if not _IDEMPOTENCY_PATTERN.fullmatch(value):
        raise InvalidIdempotencyKeyError("invalid idempotency key")
    message = f"mangasensei:{namespace}:v1\0{value}".encode()
    return hmac.new(pepper, message, hashlib.sha256).digest()
