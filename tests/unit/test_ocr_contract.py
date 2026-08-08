from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from mangasensei.ocr.models.manifest import ModelArtifact, ModelIntegrityError, verify_model


def test_model_integrity_requires_exact_size_and_sha256(tmp_path: Path) -> None:
    content = b"reviewed model fixture"
    model = tmp_path / "model.ckpt"
    model.write_bytes(content)
    artifact = ModelArtifact(
        filename="model.ckpt",
        url="https://example.test/model.ckpt",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        redistribution_status="test-only",
    )

    verify_model(model, artifact)
    model.write_bytes(b"tampered model fixture")
    with pytest.raises(ModelIntegrityError, match="checksum mismatch"):
        verify_model(model, artifact.model_copy(update={"size_bytes": model.stat().st_size}))
