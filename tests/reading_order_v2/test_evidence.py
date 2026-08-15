from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.reading_order_v2.evidence import (
    MANDATORY_MEMBERS,
    EvidenceError,
    scan_safe_text,
    validate_staging_tree,
    verify_checksums,
    write_checksums,
    write_deterministic_zip,
)


def _stage(tmp_path: Path) -> Path:
    root = tmp_path / "stage"
    source_path = "harness/source/scripts/reading_order_v2/run_arm.py"
    source_bytes = b"safe source snapshot\n"
    members = set(MANDATORY_MEMBERS) | {source_path}
    for name in members:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "checksums.sha256":
            continue
        if name == source_path:
            path.write_bytes(source_bytes)
        elif name == "harness/source-manifest.json":
            import hashlib

            value = {
                "sources": [
                    {
                        "repoPath": "scripts/reading_order_v2/run_arm.py",
                        "bundlePath": source_path,
                        "bytes": len(source_bytes),
                        "sha256": hashlib.sha256(source_bytes).hexdigest(),
                        "gitBlobSha": "1" * 40,
                    }
                ]
            }
            path.write_text(json.dumps(value), encoding="utf-8")
        elif name.endswith(".json") or name.endswith(".jsonl"):
            path.write_text("{}\n", encoding="utf-8")
        else:
            path.write_text("safe evidence\n", encoding="utf-8")
    write_checksums(root)
    return root


def test_evidence_checksum_membership_and_deterministic_zip(tmp_path) -> None:
    root = _stage(tmp_path)
    validate_staging_tree(root)
    verify_checksums(root)
    first = tmp_path / "a.zip"
    second = tmp_path / "b.zip"
    write_deterministic_zip(root, first)
    write_deterministic_zip(root, second)
    assert first.read_bytes() == second.read_bytes()


def test_evidence_rejects_unexpected_member_symlink_private_path_and_secret(tmp_path) -> None:
    root = _stage(tmp_path)
    extra = root / "extra.json"
    extra.write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceError):
        validate_staging_tree(root)
    extra.unlink()

    readme = root / "README.md"
    readme.write_text("path=/home/alice/project\n", encoding="utf-8")
    write_checksums(root)
    with pytest.raises(EvidenceError):
        validate_staging_tree(root)
    readme.write_text('"api_key": "abcdefghijklmnop"\n', encoding="utf-8")
    write_checksums(root)
    with pytest.raises(EvidenceError):
        validate_staging_tree(root)


@pytest.mark.parametrize(
    "private_path",
    [
        r"C:\Arquivos\GitHub\Projetos\MangaSensei",
        r"D:\research\artifact",
        "/mnt/data/artifact",
        "/tmp/private-output",  # noqa: S108 - intentional private-path rejection fixture
        "/home/alice/project",
    ],
)
def test_evidence_rejects_generic_private_absolute_paths(private_path: str) -> None:
    with pytest.raises(EvidenceError):
        scan_safe_text("README.md", f"path={private_path}\n".encode())


@pytest.mark.parametrize(
    "safe_text",
    [
        "scripts/reading_order_v2/run_arm.py",
        "harness/source/foo.py",
        "https://json-schema.org/draft/2020-12/schema",
        "normal non-path prose",
    ],
)
def test_evidence_allows_repository_paths_urls_and_prose(safe_text: str) -> None:
    scan_safe_text("README.md", safe_text.encode())


def test_evidence_rejects_absolute_path_in_structured_json(tmp_path) -> None:
    root = _stage(tmp_path)
    comparison = root / "comparison.json"
    comparison.write_text(json.dumps({"localPath": "/mnt/data/artifact"}), encoding="utf-8")
    write_checksums(root)
    with pytest.raises(EvidenceError):
        validate_staging_tree(root)


def test_evidence_allows_https_json_schema_value(tmp_path) -> None:
    root = _stage(tmp_path)
    comparison = root / "comparison.json"
    comparison.write_text(
        json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema"}),
        encoding="utf-8",
    )
    write_checksums(root)
    validate_staging_tree(root)


def test_evidence_rejects_checksum_mismatch(tmp_path) -> None:
    root = _stage(tmp_path)
    (root / "comparison.json").write_text('{"changed":true}\n', encoding="utf-8")
    with pytest.raises(EvidenceError):
        verify_checksums(root)


def test_evidence_rejects_source_snapshot_hash_or_size_mismatch(tmp_path) -> None:
    root = _stage(tmp_path)
    snapshot = root / "harness/source/scripts/reading_order_v2/run_arm.py"
    snapshot.write_text("tampered source\n", encoding="utf-8")
    write_checksums(root)
    with pytest.raises(EvidenceError):
        validate_staging_tree(root)
