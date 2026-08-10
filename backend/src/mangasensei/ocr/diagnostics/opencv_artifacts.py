"""Controlled artifact boundaries for OpenCV OCR diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import numpy as np

_DIAGNOSTIC_ROOT = Path("var") / "ocr-opencv-ab"
_MAX_JSON_BYTES = 64 * 1024 * 1024
_MAX_ARRAY_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ARRAY_ARCHIVE_MEMBERS = 4096
_REQUIRED_RUNTIME_KEYS = {
    "python",
    "python_implementation",
    "platform",
    "machine",
    "numpy",
    "torch",
    "pillow",
    "networkx",
    "pyclipper",
    "shapely",
    "torchvision",
    "torch_threads",
    "torch_interop_threads",
}


def validate_artifact_root(path: Path, *, repository_root: Path) -> Path:
    """Keep licensed/source-derived diagnostics under the repository's ignored root."""
    resolved_repository = repository_root.resolve()
    raw_allowed_root = resolved_repository / _DIAGNOSTIC_ROOT
    raw_path = path if path.is_absolute() else resolved_repository / path
    _reject_linked_components(raw_allowed_root, within=resolved_repository)
    _reject_linked_components(raw_path, within=resolved_repository)
    allowed_root = raw_allowed_root.resolve()
    resolved_path = raw_path.resolve()
    if not resolved_path.is_relative_to(allowed_root):
        raise ValueError("diagnostic artifacts must stay under var/ocr-opencv-ab")
    return resolved_path


def validate_fixture_path(value: str) -> PurePosixPath:
    """Validate fixture and manifest member paths across POSIX and Windows."""
    raw_parts = value.split("/")
    if (
        not value
        or "\\" in value
        or any(part in {"", ".", ".."} for part in raw_parts)
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("fixture path must be a safe relative POSIX path")
    windows_path = PureWindowsPath(value)
    if windows_path.is_absolute() or windows_path.drive or windows_path.root:
        raise ValueError("fixture path must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        raise ValueError("fixture path must be a safe relative POSIX path")
    return path


def write_fixture_artifact(
    root: Path,
    *,
    fixture_file: str,
    record: Mapping[str, object],
    arrays: Mapping[str, np.ndarray],
) -> dict[str, str]:
    """Write one controlled per-fixture record and its exact stage arrays."""
    fixture_path = validate_fixture_path(fixture_file)
    fixture_root = root / "fixtures"
    fixture_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path_digest = hashlib.sha256(fixture_file.encode("utf-8")).hexdigest()[:12]
    label = f"{'__'.join(fixture_path.with_suffix('').parts)}-{path_digest}"
    record_path = fixture_root / f"{label}.json"
    arrays_path = fixture_root / f"{label}.npz"
    payload = {**record, "file": fixture_file}
    with record_path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        output.write("\n")
    np.savez_compressed(
        arrays_path,
        **{  # type: ignore[arg-type]
            name: np.ascontiguousarray(value) for name, value in arrays.items()
        },
    )
    record_path.chmod(0o600)
    arrays_path.chmod(0o600)
    return {
        "file": fixture_file,
        "record": record_path.relative_to(root).as_posix(),
        "record_sha256": _file_sha256(record_path),
        "arrays": arrays_path.relative_to(root).as_posix(),
        "arrays_sha256": _file_sha256(arrays_path),
    }


def write_probe_manifest(
    root: Path,
    *,
    metadata: Mapping[str, object],
    fixtures: Sequence[Mapping[str, str]],
) -> Path:
    """Write the environment/integrity envelope shared by every fixture probe."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "probe.json"
    payload = {**metadata, "fixtures": [dict(fixture) for fixture in fixtures]}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def write_fixture_notice(
    root: Path,
    *,
    source_url: str,
    work: str,
    author: str,
) -> Path:
    """Keep fixture attribution and handling constraints beside derived artifacts."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "FIXTURE_NOTICE.txt"
    path.write_text(
        "\n".join(
            (
                "Controlled OCR diagnostic artifacts",
                "",
                f"Work: {work}",
                f"Author: {author}",
                f"Terms and official source: {source_url}",
                "",
                "This directory may contain source-derived detector maps, recognizer crops,",
                "OCR transcripts, and geometry from the reviewed licensed fixture corpus.",
                "It must not be committed, published as an ordinary build artifact, or retained",
                "outside the repository's reviewed fixture/license policy.",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def read_json_object(path: Path, expected_sha256: str | None = None) -> dict[str, Any]:
    content = path.read_bytes()
    if len(content) > _MAX_JSON_BYTES:
        raise ValueError(f"probe JSON exceeds its size limit: {path.name}")
    if expected_sha256 is not None and hashlib.sha256(content).hexdigest() != expected_sha256:
        raise ValueError(f"probe artifact checksum differs: {path.name}")
    raw: Any = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object: {path}")
    return raw


def read_json_object_with_sha(path: Path) -> tuple[dict[str, Any], str]:
    content = path.read_bytes()
    if len(content) > _MAX_JSON_BYTES:
        raise ValueError(f"probe JSON exceeds its size limit: {path.name}")
    raw: Any = json.loads(content)
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object: {path}")
    return raw, hashlib.sha256(content).hexdigest()


def read_arrays(path: Path, expected_sha256: str | None) -> dict[str, np.ndarray]:
    with path.open("rb") as input_file:
        actual_sha256 = hashlib.file_digest(input_file, "sha256").hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise ValueError(f"probe artifact checksum differs: {path.name}")
        input_file.seek(0)
        with zipfile.ZipFile(input_file) as archive_metadata:
            members = archive_metadata.infolist()
            if (
                len(members) > _MAX_ARRAY_ARCHIVE_MEMBERS
                or sum(member.file_size for member in members) > _MAX_ARRAY_ARCHIVE_BYTES
            ):
                raise ValueError(f"probe array archive exceeds its size limit: {path.name}")
        input_file.seek(0)
        with np.load(input_file, allow_pickle=False) as archive:
            return {name: np.asarray(archive[name]) for name in archive.files}


def fixture_entries(manifest: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    raw_entries = manifest.get("fixtures")
    if not isinstance(raw_entries, list):
        raise ValueError("probe manifest fixtures must be a list")
    entries: dict[str, dict[str, str]] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("probe fixture entry must be an object")
        entry = {
            key: str(raw_entry[key])
            for key in (
                "file",
                "record",
                "arrays",
                "record_sha256",
                "arrays_sha256",
            )
            if key in raw_entry
        }
        required_keys = {"file", "record", "arrays"}
        if int(manifest.get("schema_version", 0)) >= 2:
            required_keys.update(("record_sha256", "arrays_sha256"))
        if not required_keys.issubset(entry):
            raise ValueError("probe fixture entry is missing required paths")
        for key in ("file", "record", "arrays"):
            validate_fixture_path(entry[key])
        if entry["file"] in entries:
            raise ValueError(f"duplicate probe fixture: {entry['file']}")
        entries[entry["file"]] = entry
    if len(entries) != manifest["fixture_count"]:
        raise ValueError("probe fixture_count differs from its unique fixture inventory")
    return entries


def validate_probe_manifest(manifest: Mapping[str, Any]) -> None:
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2}:
        raise ValueError("probe manifest schema_version must be supported")
    for key in (
        "repository_sha",
        "fixture_manifest_sha256",
        "model_manifest_sha256",
        "ocr_config_digest",
    ):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError(f"probe manifest requires string invariant: {key}")
    fixture_count = manifest.get("fixture_count")
    if not isinstance(fixture_count, int) or fixture_count <= 0:
        raise ValueError("probe manifest fixture_count must be positive")
    if not isinstance(manifest.get("runtime"), dict) or not manifest["runtime"]:
        raise ValueError("probe manifest requires runtime evidence")
    opencv = manifest.get("opencv")
    if not isinstance(opencv, dict):
        raise ValueError("probe manifest requires OpenCV evidence")
    for key in (
        "distribution_version",
        "runtime_version",
        "build_information_sha256",
        "thread_count",
        "optimized",
    ):
        if key not in opencv:
            raise ValueError(f"probe OpenCV evidence is missing: {key}")
    for key in ("distribution_version", "runtime_version"):
        value = opencv[key]
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 64
            or any(
                character
                not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.!+-"
                for character in value
            )
        ):
            raise ValueError(f"probe OpenCV version is unsafe: {key}")
    if not isinstance(opencv["thread_count"], int) or opencv["thread_count"] < 0:
        raise ValueError("probe OpenCV thread_count must be non-negative")
    if not isinstance(opencv["optimized"], bool):
        raise ValueError("probe OpenCV optimized setting must be boolean")
    if schema_version == 2:
        runtime = manifest["runtime"]
        if not isinstance(runtime, dict) or not _REQUIRED_RUNTIME_KEYS.issubset(runtime):
            raise ValueError("schema 2 probe requires the complete OCR runtime evidence")
        if not isinstance(opencv.get("opencl"), bool):
            raise ValueError("schema 2 probe requires the OpenCV OpenCL setting")
        if not isinstance(opencv.get("binary_sha256"), str):
            raise ValueError("schema 2 probe requires the loaded OpenCV binary checksum")
        normalized_model_files(manifest.get("model_files"))
        _validate_source_files(manifest.get("source_files"))


def normalized_model_files(value: object) -> tuple[tuple[str, str, int], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("schema 2 probe manifest requires loaded model file evidence")
    normalized: list[tuple[str, str, int]] = []
    for entry in value:
        if not isinstance(entry, Mapping):
            raise ValueError("probe model file evidence must contain objects")
        filename = entry.get("filename")
        sha256 = entry.get("sha256")
        size_bytes = entry.get("size_bytes")
        if (
            not isinstance(filename, str)
            or "/" in filename
            or "\\" in filename
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
        ):
            raise ValueError("probe model file evidence is malformed")
        normalized.append((filename, sha256, size_bytes))
    if len({entry[0] for entry in normalized}) != len(normalized):
        raise ValueError("probe model file evidence contains duplicate filenames")
    return tuple(sorted(normalized))


def manifest_model_files(artifacts: object) -> list[dict[str, object]]:
    if not isinstance(artifacts, list) or not all(
        isinstance(artifact, dict) for artifact in artifacts
    ):
        raise ValueError("model manifest artifacts must be a list of objects")
    expected_filenames = {
        "detect-20241225.ckpt",
        "ocr_ar_48px.ckpt",
        "alphabet-all-v7.txt",
    }
    evidence = [
        {
            "filename": str(artifact["filename"]),
            "size_bytes": int(artifact["size_bytes"]),
            "sha256": str(artifact["sha256"]),
        }
        for artifact in artifacts
    ]
    if {str(item["filename"]) for item in evidence} != expected_filenames:
        raise ValueError("model manifest does not contain the exact required artifacts")
    return sorted(evidence, key=lambda item: str(item["filename"]))


def model_file_evidence(model_cache: Path, artifacts: object) -> list[dict[str, object]]:
    expected_files = manifest_model_files(artifacts)
    subdirectories = {
        "detect-20241225.ckpt": "detection",
        "ocr_ar_48px.ckpt": "ocr",
        "alphabet-all-v7.txt": "ocr",
    }
    evidence = [
        {
            "filename": str(expected["filename"]),
            "size_bytes": (
                model_cache / subdirectories[str(expected["filename"])] / str(expected["filename"])
            )
            .stat()
            .st_size,
            "sha256": _file_sha256(
                model_cache / subdirectories[str(expected["filename"])] / str(expected["filename"])
            ),
        }
        for expected in expected_files
    ]
    if any(
        actual["size_bytes"] != expected["size_bytes"] or actual["sha256"] != expected["sha256"]
        for actual, expected in zip(evidence, expected_files, strict=True)
    ):
        raise ValueError("loaded model evidence differs from the reviewed manifest")
    return evidence


def _validate_source_files(value: object) -> None:
    if not isinstance(value, dict) or not value:
        raise ValueError("schema 2 probe requires loaded source-file evidence")
    for relative_path, sha256 in value.items():
        if (
            not isinstance(relative_path, str)
            or not relative_path.endswith(".py")
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise ValueError("probe loaded source-file evidence is malformed")
        validate_fixture_path(relative_path)


def resolve_probe_member(root: Path, value: str) -> Path:
    relative = validate_fixture_path(value)
    raw_path = root.joinpath(*relative.parts)
    _reject_linked_components(raw_path, within=root)
    resolved = raw_path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("probe manifest path escapes its probe root")
    return resolved


def _reject_linked_components(path: Path, *, within: Path) -> None:
    try:
        relative = path.relative_to(within)
    except ValueError:
        return
    current = within
    for part in relative.parts:
        if part == "..":
            continue
        current /= part
        if not current.exists() and not current.is_symlink():
            continue
        attributes = getattr(os.lstat(current), "st_file_attributes", 0)
        if current.is_symlink() or attributes & 0x400:
            raise ValueError(f"diagnostic path traverses a link or reparse point: {current}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
