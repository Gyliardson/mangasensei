from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from shutil import which
from types import CodeType, ModuleType
from typing import Any

from scripts.reading_order_v3_authoring.validate import validate_corpus as validate_authoring_corpus

import mangasensei.ocr.diagnostics.reading_order_post_v2_calibration as runtime_candidate_module

from . import exercise_v3
from .canonical import sha256_path
from .historical_guard import assert_no_historical_v2_content_reuse
from .retired_guard import assert_no_retired_post_v2_v1_reuse
from .spec import REPO_ROOT
from .spec_v3 import (
    METHODOLOGY_PATH,
    validate_qualification_identity_v3,
    validate_spec_v3,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ValidatedV3Context:
    experiment_id: str
    qualification_identity: str
    execution_sha: str
    execution_tree_sha: str
    spec: dict[str, Any]
    coverage: object


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _sealed_relative(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("manifest inventory requires safe normalized POSIX relative paths")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or bool(windows.drive)
        or "." in posix.parts
        or ".." in posix.parts
        or posix.as_posix() != value
    ):
        raise ValueError("manifest inventory requires safe normalized POSIX relative paths")
    return value


def _manifest_file_record(value: object, *, where: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"file", "sha256"}:
        raise ValueError(f"manifest {where}: exact file record required")
    relative = _sealed_relative(value["file"])
    digest = value["sha256"]
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"manifest {where}: lowercase SHA-256 required")
    return relative, digest


def _manifest_inventory(manifest: object) -> dict[str, str]:
    if not isinstance(manifest, dict) or set(manifest) != {
        "schemaVersion", "corpusId", "version", "design", "pages", "inventory"
    }:
        raise ValueError("sealed manifest exact root property set required")
    if not isinstance(manifest["pages"], list) or not isinstance(manifest["inventory"], list):
        raise ValueError("sealed manifest pages and inventory arrays required")

    role_records = [_manifest_file_record(manifest["design"], where="design")]
    page_ids: set[str] = set()
    for index, page in enumerate(manifest["pages"]):
        if not isinstance(page, dict) or set(page) != {
            "pageId", "source", "image", "input", "annotation"
        }:
            raise ValueError(f"manifest page {index}: exact property set required")
        page_id = page["pageId"]
        if not isinstance(page_id, str) or page_id in page_ids:
            raise ValueError("manifest page IDs must be unique strings")
        page_ids.add(page_id)
        role_records.extend(
            _manifest_file_record(page[role], where=f"{page_id}.{role}")
            for role in ("source", "image", "input", "annotation")
        )

    inventory_records: list[tuple[str, str]] = []
    for index, record in enumerate(manifest["inventory"]):
        if not isinstance(record, dict) or set(record) != {"file", "sha256", "bytes"}:
            raise ValueError(f"manifest inventory[{index}]: exact inventory record required")
        byte_count = record["bytes"]
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 0:
            raise ValueError(f"manifest inventory[{index}]: nonnegative byte count required")
        inventory_records.append(
            _manifest_file_record(
                {"file": record["file"], "sha256": record["sha256"]},
                where=f"inventory[{index}]",
            )
        )
    inventory = dict(inventory_records)
    if len(inventory) != len(inventory_records):
        raise ValueError("manifest inventory contains duplicate paths")
    roles = dict(role_records)
    if len(roles) != len(role_records) or inventory != roles:
        raise ValueError("manifest inventory and role paths/hashes must match exactly")
    if roles.get("corpus-design.json") is None:
        raise ValueError("manifest design must use corpus-design.json")
    return inventory


def _reject_symlink_components(path: Path, *, message: str) -> None:
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise RuntimeError(message)


def _stat_identity(record: os.stat_result) -> tuple[int, int, int, int]:
    return record.st_dev, record.st_ino, record.st_size, record.st_mtime_ns


def _read_sealed_bytes(
    root: Path, relative: str, expected_sha256: str | None = None
) -> bytes:
    _reject_symlink_components(root, message="sealed corpus root has a symlinked component")
    path = root / Path(*PurePosixPath(relative).parts)
    _reject_symlink_components(path, message=f"sealed corpus path is symlinked: {relative}")
    try:
        before = path.lstat()
    except OSError as exc:
        raise ValueError(f"sealed corpus file cannot be inspected: {relative}") from exc
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"sealed corpus inventory entry is not a regular file: {relative}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"sealed corpus file cannot be safely opened: {relative}") from exc
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"sealed corpus file changed before being read: {relative}")
        payload = stream.read()
        completed = os.fstat(stream.fileno())
    after = path.lstat()
    if _stat_identity(before) != _stat_identity(opened) or _stat_identity(
        opened
    ) != _stat_identity(completed):
        raise ValueError(f"sealed corpus file changed while being read: {relative}")
    if _stat_identity(completed) != _stat_identity(after):
        raise ValueError(f"sealed corpus file changed while being read: {relative}")
    if expected_sha256 is not None and hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise ValueError(f"sealed corpus SHA-256 mismatch: {relative}")
    return payload


def _actual_sealed_files(root: Path) -> set[str]:
    files: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        parent = Path(dirpath)
        for dirname in dirnames:
            if (parent / dirname).is_symlink():
                raise ValueError("sealed corpus contains a symlinked directory")
        for filename in filenames:
            path = parent / filename
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError(f"sealed corpus contains unsafe inventory entry: {relative}")
            files.add(relative)
    return files


def _write_private_file(root: Path, relative: str, payload: bytes) -> None:
    path = root / Path(*PurePosixPath(relative).parts)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


@contextmanager
def _stage_sealed_corpus(
    corpus_root: Path,
    *,
    expected_manifest_sha256: str | None = None,
    expected_design_sha256: str | None = None,
) -> Iterator[Path]:
    manifest_bytes = _read_sealed_bytes(
        corpus_root, "manifest.json", expected_manifest_sha256
    )
    manifest = json.loads(
        manifest_bytes.decode("utf-8"), parse_constant=_reject_json_constant
    )
    inventory = _manifest_inventory(manifest)
    if (
        expected_design_sha256 is not None
        and inventory["corpus-design.json"] != expected_design_sha256
    ):
        raise ValueError("sealed corpus design hash differs from dispatch")
    if _actual_sealed_files(corpus_root) != {*inventory, "manifest.json"}:
        raise ValueError("sealed corpus filesystem inventory mismatch")
    payloads = {
        relative: _read_sealed_bytes(corpus_root, relative, digest)
        for relative, digest in inventory.items()
    }

    with tempfile.TemporaryDirectory(prefix="mangasensei-v3-corpus-") as temporary:
        staged_root = Path(temporary)
        os.chmod(staged_root, 0o700)
        for relative, payload in payloads.items():
            _write_private_file(staged_root, relative, payload)
        _write_private_file(staged_root, "manifest.json", manifest_bytes)
        validate_authoring_corpus(staged_root)
        assert_no_historical_v2_content_reuse(staged_root)
        assert_no_retired_post_v2_v1_reuse(staged_root)
        yield staged_root


def _git(*args: str) -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for v3 qualification preflight")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _git_bytes(*args: str) -> bytes:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for v3 qualification preflight")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _load_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("sealed v3 manifest must be a JSON object")
    return value


def _validate_methodology_hash(expected_methodology_sha256: str) -> None:
    if sha256_path(METHODOLOGY_PATH) != expected_methodology_sha256:
        raise ValueError("frozen v3 methodology hash mismatch")


def _loaded_module_code(module: ModuleType) -> CodeType:
    spec = getattr(module, "__spec__", None)
    loader = getattr(spec, "loader", None)
    get_code = getattr(loader, "get_code", None)
    if not callable(get_code):
        raise RuntimeError("runtime candidate loader cannot provide loaded code")
    code = get_code(module.__name__)
    if not isinstance(code, CodeType):
        raise RuntimeError("runtime candidate loader did not provide module code")
    return code


def _require_module(value: object) -> ModuleType:
    if not isinstance(value, ModuleType):
        raise RuntimeError("runtime candidate module origin is invalid: import is not a module")
    return value


def _validate_runtime_candidate(spec: dict[str, Any]) -> None:
    candidate_module = _require_module(runtime_candidate_module)
    binding = spec.get("candidateBinding")
    if not isinstance(binding, dict) or not isinstance(binding.get("path"), str):
        raise RuntimeError("frozen v3 candidate path binding is missing")
    candidate_repo_path = binding["path"]
    expected_origin = (REPO_ROOT / candidate_repo_path).resolve(strict=True)
    raw_origin = getattr(candidate_module, "__file__", None)
    if not isinstance(raw_origin, (str, Path)):
        raise RuntimeError("runtime candidate module origin is missing")
    try:
        runtime_origin = Path(raw_origin).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("runtime candidate module origin cannot be resolved") from exc
    if runtime_origin != expected_origin:
        raise RuntimeError("runtime candidate module origin does not match frozen candidate path")
    if getattr(exercise_v3, "candidate_module", None) is not candidate_module:
        raise RuntimeError("exercise_v3 candidate must be the same module object")

    runtime_bytes = runtime_origin.read_bytes()
    head_bytes = _git_bytes("show", f"HEAD:{candidate_repo_path}")
    runtime_sha256 = hashlib.sha256(runtime_bytes).digest()
    head_sha256 = hashlib.sha256(head_bytes).digest()
    if not hmac.compare_digest(runtime_sha256, head_sha256) or runtime_bytes != head_bytes:
        raise RuntimeError("runtime candidate bytes do not match HEAD-bound candidate bytes")
    expected_code = compile(
        head_bytes,
        str(expected_origin),
        "exec",
        dont_inherit=True,
        optimize=sys.flags.optimize,
    )
    if _loaded_module_code(candidate_module) != expected_code:
        raise RuntimeError("runtime candidate loaded code does not match authenticated HEAD source")


def validate_preflight_v3(
    *,
    corpus_root: Path,
    spec_path: Path,
    experiment_id: str,
    expected_spec_sha256: str,
    expected_methodology_sha256: str,
    expected_manifest_sha256: str,
    expected_design_sha256: str,
    qualification_identity: str,
    execution_sha: str,
    expected_tree_sha: str,
) -> ValidatedV3Context:
    if _git("rev-parse", "HEAD") != execution_sha:
        raise ValueError("execution SHA does not match checked-out HEAD")
    if _git("rev-parse", "HEAD^{tree}") != expected_tree_sha:
        raise ValueError("execution tree SHA mismatch")
    if _git("status", "--porcelain"):
        raise RuntimeError("v3 preflight requires a clean repository")

    spec = validate_spec_v3(spec_path, expected_sha256=expected_spec_sha256)
    if spec["experimentId"] != experiment_id:
        raise ValueError("dispatch experiment identity does not match frozen v3 spec")
    _validate_methodology_hash(expected_methodology_sha256)
    methodology = spec.get("methodology")
    if (
        not isinstance(methodology, dict)
        or methodology.get("sha256") != expected_methodology_sha256
    ):
        raise ValueError("dispatch methodology hash does not match frozen v3 spec")
    _validate_runtime_candidate(spec)

    manifest_path = corpus_root / "manifest.json"
    design_path = corpus_root / "corpus-design.json"
    if sha256_path(manifest_path) != expected_manifest_sha256:
        raise ValueError("frozen v3 corpus manifest hash mismatch")
    if sha256_path(design_path) != expected_design_sha256:
        raise ValueError("frozen v3 corpus design hash mismatch")
    manifest = _load_manifest(manifest_path)
    if manifest.get("design") != {
        "file": "corpus-design.json",
        "sha256": expected_design_sha256,
    }:
        raise ValueError("sealed v3 manifest does not bind frozen design hash")

    validate_qualification_identity_v3(
        qualification_identity,
        experiment_id=experiment_id,
        spec_sha256=expected_spec_sha256,
        methodology_sha256=expected_methodology_sha256,
        manifest_sha256=expected_manifest_sha256,
        design_sha256=expected_design_sha256,
        execution_sha=execution_sha,
        execution_tree_sha=expected_tree_sha,
    )
    return ValidatedV3Context(
        experiment_id=experiment_id,
        qualification_identity=qualification_identity,
        execution_sha=execution_sha,
        execution_tree_sha=expected_tree_sha,
        spec=spec,
        coverage=None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed preflight for v3 qualification")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    parser.add_argument("--expected-methodology-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-design-sha256", required=True)
    parser.add_argument("--qualification-identity", required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--expected-tree-sha", required=True)
    args = parser.parse_args()
    validate_preflight_v3(
        corpus_root=args.corpus_root,
        spec_path=args.experiment_spec,
        experiment_id=args.experiment_id,
        expected_spec_sha256=args.expected_spec_sha256,
        expected_methodology_sha256=args.expected_methodology_sha256,
        expected_manifest_sha256=args.expected_manifest_sha256,
        expected_design_sha256=args.expected_design_sha256,
        qualification_identity=args.qualification_identity,
        execution_sha=args.execution_sha,
        expected_tree_sha=args.expected_tree_sha,
    )


if __name__ == "__main__":
    main()
