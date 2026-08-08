"""Checksum-enforced local OCR model inventory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ModelArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    redistribution_status: str


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str
    upstream_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    artifacts: tuple[ModelArtifact, ...]

    @classmethod
    def load(cls, path: Path) -> ModelManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def artifact(self, filename: str) -> ModelArtifact:
        matching = tuple(item for item in self.artifacts if item.filename == filename)
        if len(matching) != 1:
            raise KeyError(filename)
        return matching[0]


class ModelIntegrityError(RuntimeError):
    """A local model artifact differs from the reviewed manifest."""


def verify_model(path: Path, artifact: ModelArtifact) -> None:
    if not path.is_file() or path.stat().st_size != artifact.size_bytes:
        raise ModelIntegrityError(f"model size mismatch: {artifact.filename}")
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        while chunk := model_file.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != artifact.sha256:
        raise ModelIntegrityError(f"model checksum mismatch: {artifact.filename}")


def canonical_manifest_json(manifest: ModelManifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
