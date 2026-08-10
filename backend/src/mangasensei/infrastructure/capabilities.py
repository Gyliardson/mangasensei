"""Opaque, resource-scoped HMAC capabilities."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from mangasensei.domain.capabilities import CapabilityScopeValue


@dataclass(frozen=True, slots=True)
class IssuedCapability:
    token: str
    persisted_digest: str
    expires_at: datetime


class CapabilityService:
    def __init__(self, peppers: tuple[str, ...]) -> None:
        if not peppers or any(len(pepper.encode()) < 32 for pepper in peppers):
            raise ValueError("at least one capability pepper with 32 bytes is required")
        self._peppers = tuple(pepper.encode() for pepper in peppers)

    def issue(
        self,
        *,
        resource_id: str,
        scope: CapabilityScopeValue,
        expires_at: datetime,
    ) -> IssuedCapability:
        self._validate_expiry(expires_at)
        token = secrets.token_urlsafe(32)
        digest = self._digest(self._peppers[0], token, resource_id, scope)
        return IssuedCapability(token=token, persisted_digest=digest, expires_at=expires_at)

    def verify(
        self,
        *,
        token: str,
        persisted_digest: str,
        resource_id: str,
        scope: CapabilityScopeValue,
        expires_at: datetime,
    ) -> bool:
        if not token or expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
            return False
        return any(
            hmac.compare_digest(self._digest(pepper, token, resource_id, scope), persisted_digest)
            for pepper in self._peppers
        )

    @staticmethod
    def _digest(
        pepper: bytes,
        token: str,
        resource_id: str,
        scope: CapabilityScopeValue,
    ) -> str:
        message = "\0".join((token, resource_id, scope.value)).encode()
        return hmac.new(pepper, message, hashlib.sha256).hexdigest()

    @staticmethod
    def _validate_expiry(expires_at: datetime) -> None:
        if expires_at.tzinfo is None or expires_at <= datetime.now(UTC):
            raise ValueError("capability expiry must be a future timezone-aware datetime")
