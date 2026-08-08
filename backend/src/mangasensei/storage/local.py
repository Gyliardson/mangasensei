"""Atomic content-addressed local filesystem storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from mangasensei.storage.images import ValidatedImage

_STORAGE_KEY_PATTERN = re.compile(r"^objects/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$")
_PENDING_MARKER_PATTERN = re.compile(
    r"^(?P<sha256>[0-9a-f]{64})\.(?P<nonce>[0-9a-f]{32})\.pending$"
)


class StorageCorruptionError(RuntimeError):
    """Existing content-addressed data does not match its key."""


@dataclass(frozen=True, slots=True)
class PendingStorageWrite:
    """Recovery marker for an object published before its database commit."""

    sha256: str
    storage_key: str
    marker_name: str


class LocalFilesystemStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._objects = self._root / "objects"
        self._temporary = self._root / "tmp"
        self._pending = self._root / "pending"
        self._objects.mkdir(parents=True, exist_ok=True)
        self._temporary.mkdir(parents=True, exist_ok=True)
        self._pending.mkdir(parents=True, exist_ok=True)

    async def store(self, image: ValidatedImage) -> str:
        return await asyncio.to_thread(self._store_sync, image)

    async def stage(self, image: ValidatedImage) -> PendingStorageWrite:
        """Publish an object while leaving a crash-recovery marker behind."""
        return await asyncio.to_thread(self._stage_sync, image)

    async def confirm(self, pending: PendingStorageWrite) -> None:
        """Remove a recovery marker after the corresponding database commit."""
        path = self._pending_path_for(pending)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def pending_writes(self, *, limit: int = 1000) -> tuple[PendingStorageWrite, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("pending storage write limit must be between 1 and 1000")
        return await asyncio.to_thread(self._pending_writes_sync, limit)

    async def read(self, key: str) -> bytes:
        path = self._path_for_key(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise FileNotFoundError("stored image was not found") from exc

    async def delete(self, key: str) -> None:
        path = self._path_for_key(key)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def probe(self) -> None:
        await asyncio.to_thread(self._probe_sync)

    def _stage_sync(self, image: ValidatedImage) -> PendingStorageWrite:
        marker_name = f"{image.sha256}.{uuid4().hex}.pending"
        marker_path = self._pending / marker_name
        with marker_path.open("xb") as marker:
            marker.write(b"pending\n")
            marker.flush()
            os.fsync(marker.fileno())
        try:
            storage_key = self._store_sync(image)
        except Exception:
            marker_path.unlink(missing_ok=True)
            raise
        return PendingStorageWrite(
            sha256=image.sha256,
            storage_key=storage_key,
            marker_name=marker_name,
        )

    def _store_sync(self, image: ValidatedImage) -> str:
        key = self._storage_key_for_digest(image.sha256)
        destination = self._path_for_key(key)
        destination.parent.mkdir(parents=True, exist_ok=True)

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self._temporary, prefix="upload-", delete=False
            ) as temporary:
                temporary.write(image.content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                self._verify_existing(destination, image.sha256, len(image.content))
            return key
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _pending_writes_sync(self, limit: int) -> tuple[PendingStorageWrite, ...]:
        pending: list[PendingStorageWrite] = []
        for path in sorted(self._pending.iterdir(), key=lambda candidate: candidate.name):
            match = _PENDING_MARKER_PATTERN.fullmatch(path.name)
            if match is None or not path.is_file():
                continue
            sha256 = match.group("sha256")
            pending.append(
                PendingStorageWrite(
                    sha256=sha256,
                    storage_key=self._storage_key_for_digest(sha256),
                    marker_name=path.name,
                )
            )
            if len(pending) >= limit:
                break
        return tuple(pending)

    def _pending_path_for(self, pending: PendingStorageWrite) -> Path:
        match = _PENDING_MARKER_PATTERN.fullmatch(pending.marker_name)
        if match is None or match.group("sha256") != pending.sha256:
            raise ValueError("invalid pending storage marker")
        if pending.storage_key != self._storage_key_for_digest(pending.sha256):
            raise ValueError("pending storage marker does not match its content key")
        return self._pending / pending.marker_name

    def _probe_sync(self) -> None:
        probe_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self._temporary, prefix="readiness-", delete=False
            ) as probe:
                probe.write(b"ready")
                probe.flush()
                os.fsync(probe.fileno())
                probe_path = Path(probe.name)
        finally:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)

    @staticmethod
    def _storage_key_for_digest(sha256: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ValueError("invalid storage digest")
        return f"objects/{sha256[:2]}/{sha256[2:4]}/{sha256}"

    def _path_for_key(self, key: str) -> Path:
        if not _STORAGE_KEY_PATTERN.fullmatch(key):
            raise ValueError("invalid storage key")
        return self._root.joinpath(*key.split("/"))

    @staticmethod
    def _verify_existing(path: Path, expected_sha256: str, expected_size: int) -> None:
        if path.stat().st_size != expected_size:
            raise StorageCorruptionError("stored image size does not match its content key")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not secrets_compare(digest, expected_sha256):
            raise StorageCorruptionError("stored image digest does not match its content key")


def secrets_compare(left: str, right: str) -> bool:
    """Keep digest comparison constant-time without exposing storage internals."""
    import hmac

    return hmac.compare_digest(left, right)
