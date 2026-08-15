from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.reading_order_v2.evidence import (
    EvidenceError,
    build_evidence_members,
    required_members,
    validate_checksum_coverage,
    write_evidence_zip,
)


def payloads() -> dict[str, bytes]:
    return {
        name: (b"README\n" if name == "README.md" else b"{}\n")
        for name in required_members()
        if name != "checksums.sha256"
    }


def test_build_evidence_requires_exact_mandatory_members_and_complete_checksums() -> None:
    members = build_evidence_members(
        payloads(), source_snapshots={"x.py": b"print('x')\n"}
    )
    assert set(required_members()) <= set(members)
    assert "harness/source/x.py" in members
    validate_checksum_coverage(members)


def test_evidence_rejects_private_absolute_paths() -> None:
    values = payloads()
    values["repository.json"] = (
        json.dumps({"path": "/home/alice/repo"}).encode("utf-8") + b"\n"
    )
    with pytest.raises(EvidenceError, match="absolute path"):
        build_evidence_members(values, source_snapshots={})


def test_evidence_rejects_secret_like_material() -> None:
    values = payloads()
    values["environment.json"] = (
        json.dumps({"api_key": "super-secret-value"}).encode("utf-8") + b"\n"
    )
    with pytest.raises(EvidenceError, match="secret-like"):
        build_evidence_members(values, source_snapshots={})


def test_evidence_zip_is_deterministic_and_validates_membership(tmp_path: Path) -> None:
    members = build_evidence_members(
        payloads(), source_snapshots={"a.py": b"x=1\n"}
    )
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    first_sha = write_evidence_zip(first, members)
    second_sha = write_evidence_zip(
        second, dict(reversed(list(members.items())))
    )
    assert first_sha == second_sha
    assert first.read_bytes() == second.read_bytes()
