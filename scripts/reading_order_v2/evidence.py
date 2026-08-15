from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

ARMS = (
    "A0_B0_CONTROL",
    "A1_B0_PANEL_ONLY",
    "A0_B1_ORDER_ONLY",
    "A1_B1_COMBINED",
)
MANDATORY_MEMBERS = {
    "experiment-spec.json",
    "repository.json",
    "environment.json",
    "heldout/corpus-design.json",
    "heldout/manifest.json",
    "inputs/region-fixtures.json",
    "inputs/image-hashes.json",
    "harness/source-manifest.json",
    "comparison.json",
    "verdict.json",
    "README.md",
    "checksums.sha256",
} | {
    f"arms/{arm}/{name}"
    for arm in ARMS
    for name in ("diagnostics.jsonl", "ordering.json", "scores.json", "repeat-hashes.json")
}
FORBIDDEN_SUFFIXES = {
    ".ckpt", ".pt", ".pth", ".onnx", ".safetensors",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff",
}
_SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|password|secret|authorization)"
    r"\s*[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{12,}"
)
_PRIVATE_PATH_RE = re.compile(r"(?:/(?:home|Users)/[^\s\"']+|[A-Za-z]:\\Users\\[^\s\"']+)")


class EvidenceError(ValueError):
    pass


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_member(name: str) -> None:
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts or name.startswith("/"):
        raise EvidenceError(f"unsafe evidence member path: {name}")
    if "\\" in name:
        raise EvidenceError(f"evidence members must use POSIX separators: {name}")
    if pure.suffix.lower() in FORBIDDEN_SUFFIXES:
        raise EvidenceError(f"forbidden evidence artifact type: {name}")


def _source_paths(source_manifest: dict[str, object]) -> set[str]:
    raw = source_manifest.get("sources")
    if not isinstance(raw, list):
        raise EvidenceError("source-manifest.sources must be an array")
    result: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise EvidenceError("malformed source-manifest source entry")
        repo_path = item.get("repoPath")
        bundle_path = item.get("bundlePath")
        byte_count = item.get("bytes")
        digest = item.get("sha256")
        git_blob_sha = item.get("gitBlobSha")
        if not isinstance(repo_path, str) or not repo_path or repo_path.startswith(("/", "\\")):
            raise EvidenceError("source-manifest repoPath must be repository-relative")
        _safe_member(repo_path.replace("\\", "/"))
        if not isinstance(bundle_path, str):
            raise EvidenceError("source-manifest bundlePath must be a string")
        if not bundle_path.startswith("harness/source/"):
            raise EvidenceError(f"source snapshot outside harness/source: {bundle_path}")
        _safe_member(bundle_path)
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise EvidenceError("source-manifest bytes must be a non-negative integer")
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise EvidenceError("source-manifest sha256 must be lowercase SHA-256")
        if git_blob_sha is not None and (
            not isinstance(git_blob_sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", git_blob_sha) is None
        ):
            raise EvidenceError("source-manifest gitBlobSha must be lowercase Git SHA-1")
        if bundle_path in result:
            raise EvidenceError(f"duplicate source snapshot path: {bundle_path}")
        result.add(bundle_path)
    return result


def _verify_source_snapshots(root: Path, source_manifest: dict[str, object]) -> None:
    raw = source_manifest["sources"]
    assert isinstance(raw, list)
    for item in raw:
        assert isinstance(item, dict)
        bundle_path = item["bundlePath"]
        assert isinstance(bundle_path, str)
        snapshot = root / PurePosixPath(bundle_path)
        if not snapshot.is_file():
            raise EvidenceError(f"missing source snapshot: {bundle_path}")
        expected_bytes = item["bytes"]
        expected_digest = item["sha256"]
        if snapshot.stat().st_size != expected_bytes:
            raise EvidenceError(f"source snapshot byte mismatch: {bundle_path}")
        if sha256_path(snapshot) != expected_digest:
            raise EvidenceError(f"source snapshot SHA-256 mismatch: {bundle_path}")


def expected_members(source_manifest: dict[str, object]) -> set[str]:
    return set(MANDATORY_MEMBERS) | _source_paths(source_manifest)


def scan_safe_text(name: str, data: bytes) -> None:
    if name.endswith((".json", ".jsonl", ".md", ".py", ".mjs", ".sha256")):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise EvidenceError(f"text evidence is not UTF-8: {name}") from error
        if _PRIVATE_PATH_RE.search(text):
            raise EvidenceError(f"private absolute path detected in {name}")
        if _SECRET_RE.search(text):
            raise EvidenceError(f"secret-like assignment detected in {name}")


def validate_staging_tree(root: Path) -> dict[str, object]:
    manifest_path = root / "harness" / "source-manifest.json"
    if not manifest_path.is_file():
        raise EvidenceError("missing harness/source-manifest.json")
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(source_manifest, dict):
        raise EvidenceError("source-manifest must be an object")
    expected = expected_members(source_manifest)
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise EvidenceError(f"symlink forbidden in evidence: {path.relative_to(root)}")
        if not path.is_file():
            continue
        name = path.relative_to(root).as_posix()
        _safe_member(name)
        actual.add(name)
        data = path.read_bytes()
        if name.endswith((".json", ".jsonl")):
            for line_number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    json.loads(line)
                except json.JSONDecodeError as error:
                    raise EvidenceError(f"malformed JSON in {name}:{line_number}") from error
        scan_safe_text(name, data)
    if actual != expected:
        raise EvidenceError(
            "evidence member mismatch: "
            f"missing={sorted(expected-actual)}, extra={sorted(actual-expected)}"
        )
    _verify_source_snapshots(root, source_manifest)
    return source_manifest


def write_checksums(root: Path) -> None:
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    )
    lines = [f"{sha256_path(path)}  {path.relative_to(root).as_posix()}" for path in files]
    (root / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def verify_checksums(root: Path) -> None:
    manifest = root / "checksums.sha256"
    seen: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EvidenceError("malformed checksums.sha256")
        _safe_member(name)
        if name == "checksums.sha256" or name in seen:
            raise EvidenceError("invalid checksum membership")
        seen.add(name)
        path = root / PurePosixPath(name)
        if not path.is_file() or sha256_path(path) != digest:
            raise EvidenceError(f"checksum mismatch: {name}")
    expected = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "checksums.sha256"
    }
    if seen != expected:
        raise EvidenceError("checksums.sha256 does not cover every other evidence member")


def write_deterministic_zip(root: Path, destination: Path) -> None:
    validate_staging_tree(root)
    verify_checksums(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(
            (path for path in root.rglob("*") if path.is_file()),
            key=lambda item: item.relative_to(root).as_posix(),
        ):
            name = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise EvidenceError("ZIP CRC verification failed")
        if archive.namelist() != sorted(archive.namelist()):
            raise EvidenceError("ZIP members are not lexicographically ordered")
