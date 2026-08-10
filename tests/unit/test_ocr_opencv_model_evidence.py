from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from mangasensei.ocr.diagnostics.opencv_artifacts import (
    model_file_evidence,
    read_arrays,
    write_fixture_artifact,
)


def test_model_file_evidence_rejects_same_size_checksum_drift(tmp_path: Path) -> None:
    files = {
        "detect-20241225.ckpt": ("detection", b"detector"),
        "ocr_ar_48px.ckpt": ("ocr", b"recognizer"),
        "alphabet-all-v7.txt": ("ocr", b"alphabet"),
    }
    artifacts = [
        {
            "filename": filename,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for filename, (_, content) in files.items()
    ]
    for filename, (subdirectory, content) in files.items():
        parent = tmp_path / subdirectory
        parent.mkdir(parents=True, exist_ok=True)
        (parent / filename).write_bytes(content)

    evidence = model_file_evidence(tmp_path, artifacts)

    assert {item["filename"] for item in evidence} == set(files)
    detector_path = tmp_path / "detection" / "detect-20241225.ckpt"
    detector_path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="loaded model evidence differs"):
        model_file_evidence(tmp_path, artifacts)


def test_array_archive_checksum_rejects_npz_tampering(tmp_path: Path) -> None:
    entry = write_fixture_artifact(
        tmp_path,
        fixture_file="v01/synthetic.jpg",
        record={},
        arrays={"stage": np.asarray([[1, 2]], dtype=np.uint8)},
    )
    arrays_path = tmp_path / entry["arrays"]
    with arrays_path.open("ab") as output:
        output.write(b"tampered")

    with pytest.raises(ValueError, match="artifact checksum"):
        read_arrays(arrays_path, entry["arrays_sha256"])
