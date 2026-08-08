"""Atomic, checksum-enforced downloads for local-only OCR model artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import time
from pathlib import Path

import httpx

from mangasensei.ocr.models.manifest import (
    ModelArtifact,
    ModelIntegrityError,
    ModelManifest,
    verify_model,
)

_ARTIFACT_DIRECTORIES = {
    "detect-20241225.ckpt": "detection",
    "ocr_ar_48px.ckpt": "ocr",
    "alphabet-all-v7.txt": "ocr",
}


def default_manifest_path() -> Path:
    return Path(__file__).with_name("manifest.json")


def artifact_target(model_cache: Path, artifact: ModelArtifact) -> Path:
    try:
        directory = _ARTIFACT_DIRECTORIES[artifact.filename]
    except KeyError as exc:
        raise ModelIntegrityError(f"unknown model destination: {artifact.filename}") from exc
    return model_cache / directory / artifact.filename


async def download_models(
    model_cache: Path,
    *,
    manifest_path: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[Path, ...]:
    manifest = ModelManifest.load(manifest_path or default_manifest_path())
    targets: list[Path] = []
    for artifact in manifest.artifacts:
        target = artifact_target(model_cache, artifact)
        await download_artifact(artifact, target, client=client)
        targets.append(target)
    return tuple(targets)


def verify_models(
    model_cache: Path, *, manifest_path: Path | None = None
) -> tuple[Path, ...]:
    manifest = ModelManifest.load(manifest_path or default_manifest_path())
    targets = tuple(artifact_target(model_cache, artifact) for artifact in manifest.artifacts)
    for target, artifact in zip(targets, manifest.artifacts, strict=True):
        verify_model(target, artifact)
    return targets


async def download_artifact(
    artifact: ModelArtifact,
    target: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    if client is None:
        timeout = httpx.Timeout(300.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as owned_client:
            return await download_artifact(artifact, target, client=owned_client)

    if _is_verified(target, artifact):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f"{target.name}.lock")
    owns_lock = await _acquire_lock(lock_path, target, artifact)
    if not owns_lock:
        return False

    part_path = target.with_name(
        f"{target.name}.part-{os.getpid()}-{secrets.token_hex(4)}"
    )
    try:
        if _is_verified(target, artifact):
            return False
        digest = hashlib.sha256()
        size = 0
        async with client.stream(
            "GET", artifact.url, follow_redirects=True, timeout=300.0
        ) as response:
            response.raise_for_status()
            if response.url.scheme != "https":
                raise ModelIntegrityError("model download redirected outside HTTPS")
            with part_path.open("xb") as output:
                async for chunk in response.aiter_bytes(1024 * 1024):
                    size += len(chunk)
                    if size > artifact.size_bytes:
                        raise ModelIntegrityError(f"model size mismatch: {artifact.filename}")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if size != artifact.size_bytes:
            raise ModelIntegrityError(f"model size mismatch: {artifact.filename}")
        if digest.hexdigest() != artifact.sha256:
            raise ModelIntegrityError(f"model checksum mismatch: {artifact.filename}")
        os.replace(part_path, target)
        verify_model(target, artifact)
        return True
    finally:
        part_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)


def _is_verified(path: Path, artifact: ModelArtifact) -> bool:
    try:
        verify_model(path, artifact)
    except ModelIntegrityError:
        return False
    return True


async def _acquire_lock(
    lock_path: Path,
    target: Path,
    artifact: ModelArtifact,
    *,
    wait_seconds: float = 600.0,
) -> bool:
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _is_verified(target, artifact):
                return False
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for model lock: {artifact.filename}"
                ) from None
            try:
                if time.time() - os.stat(lock_path).st_mtime > 3_600:
                    os.unlink(lock_path)
                    continue
            except FileNotFoundError:
                continue
            await asyncio.sleep(0.25)
        else:
            with os.fdopen(descriptor, "w", encoding="ascii") as lock_file:
                lock_file.write(str(os.getpid()))
            return True
