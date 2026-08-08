"""Atomic content-addressed local filesystem storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path

from mangasensei.storage.images import ValidatedImage

_STORAGE_KEY_PATTERN = re.compile(r"^objects/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$")


class StorageCorruptionError(RuntimeError):
    """Existing content-addressed data does not match its key."""


class LocalFilesystemStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._objects = self._root / "objects"
        self._temporary = self._root / "tmp"
        self._objects.mkdir(parents=True, exist_ok=True)
        self._temporary.mkdir(parents=True, exist_ok=True)

    async def store(self, image: ValidatedImage) -> str:
        return await asyncio.to_thread(self._store_sync, image)

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

    def _store_sync(self, image: ValidatedImage) -> str:
        key = f"objects/{image.sha256[:2]}/{image.sha256[2:4]}/{image.sha256}"
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
