"""Digest-scoped PostgreSQL locks for filesystem/database convergence."""

from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_LOCK_DOMAIN = b"mangasensei:image-blob-lock:v1\0"


async def acquire_image_blob_lock(session: AsyncSession, sha256: bytes) -> None:
    """Serialize filesystem-affecting work for one content digest until commit."""
    if len(sha256) != 32:
        raise ValueError("image blob digest must be exactly 32 bytes")
    lock_digest = hashlib.sha256(_LOCK_DOMAIN + sha256).digest()
    lock_key = int.from_bytes(lock_digest[:8], byteorder="big", signed=True)
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )
