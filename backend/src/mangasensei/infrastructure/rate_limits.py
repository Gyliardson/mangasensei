"""PostgreSQL-backed fixed-window rate limiting without raw client identifiers."""

from __future__ import annotations

import hashlib
import hmac
import re

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.infrastructure.database.operational_models import RateLimitBucketRecord

_ACTION_PATTERN = re.compile(r"^[a-z_]{1,32}$")


class PostgreSQLRateLimiter:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        pepper: str,
    ) -> None:
        self._sessions = sessions
        self._pepper = pepper.encode()

    async def allow(self, *, client_key: str, action: str, limit: int) -> bool:
        if not _ACTION_PATTERN.fullmatch(action):
            raise ValueError("invalid rate-limit action")
        if not 1 <= limit <= 10_000:
            raise ValueError("rate limit must be between 1 and 10000")
        digest = hmac.new(
            self._pepper,
            f"mangasensei:rate-limit:v1\0{client_key}".encode(),
            hashlib.sha256,
        ).digest()
        statement = (
            insert(RateLimitBucketRecord)
            .values(
                key_digest=digest,
                action=action,
                window_start=func.date_trunc("minute", func.now()),
                request_count=1,
            )
            .on_conflict_do_update(
                index_elements=[
                    RateLimitBucketRecord.key_digest,
                    RateLimitBucketRecord.action,
                    RateLimitBucketRecord.window_start,
                ],
                set_={
                    "request_count": RateLimitBucketRecord.request_count + 1,
                    "updated_at": func.now(),
                },
            )
            .returning(RateLimitBucketRecord.request_count)
        )
        async with self._sessions.begin() as session:
            count = (await session.execute(statement)).scalar_one()
        return count <= limit
