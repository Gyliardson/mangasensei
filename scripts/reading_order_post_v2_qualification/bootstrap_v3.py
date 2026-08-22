from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import ModuleType
from typing import cast

SPEC_REPO_PATH = "scripts/reading_order_post_v2_qualification/spec/experiment-spec-v3.json"
BOOTSTRAP_REPO_PATH = "scripts/reading_order_post_v2_qualification/bootstrap_v3.py"
_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _safe_repo_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("authenticated source path must be a normalized POSIX relative path")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or bool(windows.drive)
        or "." in posix.parts
        or ".." in posix.parts
        or posix.as_posix() != value
    ):
        raise ValueError("authenticated source path must be a normalized POSIX relative path")
    return value


def _git(git_root: Path, *args: str, text: bool = True) -> str | bytes:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required for authenticated v3 source staging")
    result = subprocess.run(  # noqa: S603
        [executable, "--no-replace-objects", "-C", str(git_root), *args],
        check=True,
        capture_output=True,
        text=text,
    )
    return cast(str | bytes, result.stdout.strip() if text else result.stdout)


def _git_text(git_root: Path, *args: str) -> str:
    value = _git(git_root, *args)
    if not isinstance(value, str):
        raise RuntimeError("git text operation returned bytes")
    return value


def _git_bytes(git_root: Path, *args: str) -> bytes:
    value = _git(git_root, *args, text=False)
    if not isinstance(value, bytes):
        raise RuntimeError("git byte operation returned text")
    return value


def _git_blob_sha(payload: bytes) -> str:
    return hashlib.sha1(  # noqa: S324 - Git SHA-1 object identity
        b"blob " + str(len(payload)).encode("ascii") + b"\0" + payload
    ).hexdigest()


def _verified_blob_bytes(git_root: Path, oid: str) -> bytes:
    payload = _git_bytes(git_root, "cat-file", "blob", oid)
    if _git_blob_sha(payload) != oid:
        raise RuntimeError("Git returned bytes that do not match the requested blob")
    return payload


def _write_private_file(destination: Path, relative: str, payload: bytes) -> None:
    path = destination / Path(*PurePosixPath(relative).parts)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
    os.chmod(path, 0o400)


def _validate_git_identities(execution_sha: str, expected_tree_sha: str) -> None:
    if _HEX40_RE.fullmatch(execution_sha) is None or _HEX40_RE.fullmatch(expected_tree_sha) is None:
        raise ValueError("authenticated source requires lowercase 40-character Git identities")


def _blob_oid(git_root: Path, execution_sha: str, relative: str) -> str:
    revision = f"{execution_sha}:{relative}"
    oid = _git_text(
        git_root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        revision,
    )
    if _git_text(git_root, "cat-file", "-t", oid) != "blob":
        raise ValueError(f"authenticated source path is not a Git blob: {relative}")
    return oid


def materialize_git_snapshot(
    *,
    git_root: Path,
    execution_sha: str,
    expected_tree_sha: str,
    bindings: Mapping[str, str],
    destination: Path,
) -> None:
    _validate_git_identities(execution_sha, expected_tree_sha)
    resolved_commit = _git_text(
        git_root,
        "rev-parse",
        "--verify",
        "--end-of-options",
        f"{execution_sha}^{{commit}}",
    )
    if resolved_commit != execution_sha:
        raise ValueError("execution SHA does not resolve to the requested commit")
    if (
        _git_text(
            git_root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{execution_sha}^{{tree}}",
        )
        != expected_tree_sha
    ):
        raise ValueError("execution tree SHA mismatch")

    payloads: dict[str, bytes] = {}
    for raw_path, expected_blob in bindings.items():
        relative = _safe_repo_path(raw_path)
        if _HEX40_RE.fullmatch(expected_blob) is None:
            raise ValueError(f"authenticated source has malformed Git blob: {relative}")
        actual_blob = _blob_oid(git_root, execution_sha, relative)
        if actual_blob != expected_blob:
            raise ValueError(f"authenticated source Git blob mismatch: {relative}")
        payloads[relative] = _verified_blob_bytes(git_root, actual_blob)

    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise RuntimeError("authenticated source destination must be an empty directory")
        os.chmod(destination, 0o700)
    else:
        destination.mkdir(mode=0o700)
    for relative, payload in payloads.items():
        _write_private_file(destination, relative, payload)


def _object(value: bytes, *, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def _binding(value: object, *, label: str) -> tuple[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} binding is missing")
    path = _safe_repo_path(value.get("path"))
    blob = value.get("gitBlobSha")
    if not isinstance(blob, str) or _HEX40_RE.fullmatch(blob) is None:
        raise ValueError(f"{label} Git blob is malformed")
    return path, blob


def _qualification_bindings(
    *, git_root: Path, execution_sha: str, expected_spec_sha256: str
) -> dict[str, str]:
    if _HEX40_RE.fullmatch(execution_sha) is None:
        raise ValueError("execution SHA must be a lowercase 40-character Git identity")
    if _HEX64_RE.fullmatch(expected_spec_sha256) is None:
        raise ValueError("expected v3 spec SHA-256 is malformed")
    spec_blob = _blob_oid(git_root, execution_sha, SPEC_REPO_PATH)
    spec_bytes = _verified_blob_bytes(git_root, spec_blob)
    if hashlib.sha256(spec_bytes).hexdigest() != expected_spec_sha256:
        raise ValueError("authenticated v3 experiment spec SHA-256 mismatch")
    spec = _object(spec_bytes, label="v3 experiment spec")
    closure = spec.get("reviewedV3SourceClosure")
    if not isinstance(closure, list) or not closure:
        raise ValueError("reviewed v3 source closure is missing")

    bindings: dict[str, str] = {}
    for index, item in enumerate(closure):
        if not isinstance(item, dict) or set(item) != {"path", "role", "gitBlobSha"}:
            raise ValueError(f"reviewed v3 source closure binding {index} is malformed")
        path, blob = _binding(item, label=f"reviewed source {index}")
        if path in bindings:
            raise ValueError("reviewed v3 source closure contains duplicate paths")
        bindings[path] = blob
    if BOOTSTRAP_REPO_PATH not in bindings:
        raise ValueError("reviewed v3 source closure does not bind the bootstrap")

    bindings[SPEC_REPO_PATH] = spec_blob
    for key in ("baseV2Spec", "methodology", "candidateBinding"):
        path, blob = _binding(spec.get(key), label=key)
        existing = bindings.get(path)
        if existing is not None and existing != blob:
            raise ValueError(f"conflicting authenticated source binding: {path}")
        bindings[path] = blob

    base_path, _base_blob = _binding(spec.get("baseV2Spec"), label="baseV2Spec")
    base = _object(
        _verified_blob_bytes(git_root, _blob_oid(git_root, execution_sha, base_path)),
        label="base v2 experiment spec",
    )
    legacy_path, legacy_blob = _binding(base.get("baseSpec"), label="legacy base spec")
    bindings[legacy_path] = legacy_blob

    infrastructure = base.get("infrastructureOverlay")
    if not isinstance(infrastructure, dict):
        raise ValueError("base v2 infrastructure overlay is missing")
    overrides = infrastructure.get("sourceBindingOverrides")
    if not isinstance(overrides, list):
        raise ValueError("base v2 source binding overrides are missing")
    retired = [
        item
        for item in overrides
        if isinstance(item, dict)
        and item.get("path") == "assets/reading-order-post-v2/heldout-v1/manifest.json"
    ]
    if len(retired) != 1:
        raise ValueError("retired corpus hash ledger binding is missing")
    retired_path, retired_blob = _binding(retired[0], label="retired corpus hash ledger")
    bindings[retired_path] = retired_blob
    return bindings


def _external_roots(git_root: Path) -> tuple[Path, ...]:
    candidates: list[Path] = []
    executable = Path(sys.executable).resolve()
    if executable.is_relative_to(git_root):
        raise RuntimeError("isolated interpreter cannot come from the mutable checkout")
    environment_root = executable.parent.parent
    if (environment_root / "pyvenv.cfg").is_file():
        if os.name == "nt":
            candidates.append(environment_root / "Lib" / "site-packages")
        else:
            candidates.append(
                environment_root
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            )
    paths = sysconfig.get_paths()
    candidates.extend(Path(paths[key]) for key in ("purelib", "platlib"))
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(git_root):
            raise RuntimeError("external dependency root cannot come from the mutable checkout")
        if resolved.is_dir() and resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


def _clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
    }


def _require_isolated_interpreter() -> None:
    if not sys.flags.isolated or not sys.flags.no_site:
        raise RuntimeError("v3 bootstrap requires Python -I -S")


def _replace_argument(arguments: list[str], name: str, value: str) -> None:
    try:
        index = arguments.index(name)
    except ValueError as exc:
        raise ValueError(f"required qualification argument is missing: {name}") from exc
    if index + 1 >= len(arguments):
        raise ValueError(f"qualification argument has no value: {name}")
    arguments[index + 1] = value


def _worker_command(
    *,
    mode: str,
    source_root: Path,
    git_root: Path,
    execution_sha: str,
    expected_tree_sha: str,
    expected_spec_sha256: str,
    arguments: Sequence[str],
) -> list[str]:
    command = [
        sys.executable,
        "-I",
        "-S",
        str(source_root / BOOTSTRAP_REPO_PATH),
        mode,
        "--source-root",
        str(source_root),
        "--git-root",
        str(git_root),
        "--execution-sha",
        execution_sha,
        "--expected-tree-sha",
        expected_tree_sha,
        "--expected-spec-sha256",
        expected_spec_sha256,
    ]
    command.append("--")
    command.extend(arguments)
    return command


@contextmanager
def authenticated_source_snapshot(
    *,
    git_root: Path,
    execution_sha: str,
    expected_tree_sha: str,
    expected_spec_sha256: str,
) -> Iterator[Path]:
    _validate_git_identities(execution_sha, expected_tree_sha)
    bindings = _qualification_bindings(
        git_root=git_root,
        execution_sha=execution_sha,
        expected_spec_sha256=expected_spec_sha256,
    )
    with tempfile.TemporaryDirectory(prefix="mangasensei-v3-source-") as temporary:
        source_root = Path(temporary)
        materialize_git_snapshot(
            git_root=git_root,
            execution_sha=execution_sha,
            expected_tree_sha=expected_tree_sha,
            bindings=bindings,
            destination=source_root,
        )
        yield source_root


def _validate_external_bootstrap(bootstrap_origin: Path, expected_blob: str) -> None:
    if _git_blob_sha(bootstrap_origin.read_bytes()) != expected_blob:
        raise RuntimeError("external bootstrap does not match execution_sha")


def _run_parent(arguments: Sequence[str]) -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--git-root", type=Path, required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--expected-tree-sha", required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    known, remaining = parser.parse_known_args(arguments)
    git_root = known.git_root.resolve(strict=True)
    if not git_root.is_dir():
        raise ValueError("Git root must be an existing directory")
    canonical_git_root = Path(_git_text(git_root, "rev-parse", "--show-toplevel")).resolve(
        strict=True
    )
    if canonical_git_root != git_root:
        raise ValueError("Git root must be the canonical repository top-level directory")
    bootstrap_origin = Path(__file__).resolve(strict=True)
    if bootstrap_origin.is_relative_to(canonical_git_root):
        raise RuntimeError(
            "parent bootstrap must be extracted from execution_sha outside the checkout"
        )
    bindings = _qualification_bindings(
        git_root=canonical_git_root,
        execution_sha=known.execution_sha,
        expected_spec_sha256=known.expected_spec_sha256,
    )
    _validate_external_bootstrap(bootstrap_origin, bindings[BOOTSTRAP_REPO_PATH])
    forwarded = list(arguments)
    with authenticated_source_snapshot(
        git_root=canonical_git_root,
        execution_sha=known.execution_sha,
        expected_tree_sha=known.expected_tree_sha,
        expected_spec_sha256=known.expected_spec_sha256,
    ) as source_root:
        _replace_argument(
            forwarded,
            "--experiment-spec",
            str(source_root / SPEC_REPO_PATH),
        )
        forwarded = [
            value
            for index, value in enumerate(forwarded)
            if not (
                value == "--git-root"
                or (index > 0 and forwarded[index - 1] == "--git-root")
            )
        ]
        del remaining
        subprocess.run(  # noqa: S603
            _worker_command(
                mode="worker-parent",
                source_root=source_root,
                git_root=canonical_git_root,
                execution_sha=known.execution_sha,
                expected_tree_sha=known.expected_tree_sha,
                expected_spec_sha256=known.expected_spec_sha256,
                arguments=forwarded,
            ),
            cwd=source_root,
            env=_clean_environment(),
            check=True,
        )


def _split_worker_arguments(arguments: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    try:
        separator = arguments.index("--")
    except ValueError as exc:
        raise ValueError("authenticated worker argument separator is missing") from exc
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--git-root", type=Path, required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--expected-tree-sha", required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    known = parser.parse_args(arguments[:separator])
    return known, list(arguments[separator + 1 :])


def _configure_worker_paths(source_root: Path, git_root: Path) -> None:
    sys.dont_write_bytecode = True
    expected_bootstrap = (source_root / BOOTSTRAP_REPO_PATH).resolve(strict=True)
    if Path(__file__).resolve(strict=True) != expected_bootstrap:
        raise RuntimeError("authenticated worker must run the staged bootstrap")
    project_roots = (source_root, source_root / "backend" / "src")
    if any(not root.is_dir() for root in project_roots):
        raise RuntimeError("authenticated project import roots are incomplete")
    original = [Path(item).resolve() for item in sys.path if item]
    if any(root.is_relative_to(git_root) for root in original):
        raise RuntimeError("worker import path cannot come from the mutable checkout")
    approved_external = list(_external_roots(git_root))
    sys.path[:] = [
        *(str(root.resolve(strict=True)) for root in project_roots),
        *(str(root) for root in original),
        *(str(root) for root in approved_external if root not in original),
    ]
    scripts_package = ModuleType("scripts")
    scripts_package.__path__ = [str((source_root / "scripts").resolve(strict=True))]
    scripts_package.__package__ = "scripts"
    scripts_package.__spec__ = importlib.machinery.ModuleSpec(
        "scripts", loader=None, is_package=True
    )
    sys.modules["scripts"] = scripts_package


def _snapshot_files(source_root: Path) -> set[str]:
    files: set[str] = set()
    for parent, directories, names in os.walk(source_root, topdown=True, followlinks=False):
        parent_path = Path(parent)
        if any((parent_path / name).is_symlink() for name in directories):
            raise RuntimeError("authenticated source snapshot contains a symlinked directory")
        for name in names:
            path = parent_path / name
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("authenticated source snapshot contains an unsafe file")
            files.add(path.relative_to(source_root).as_posix())
    return files


def _validate_worker_snapshot(
    *,
    source_root: Path,
    git_root: Path,
    execution_sha: str,
    expected_tree_sha: str,
    expected_spec_sha256: str,
) -> None:
    _validate_git_identities(execution_sha, expected_tree_sha)
    if (
        _git_text(
            git_root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{execution_sha}^{{commit}}",
        )
        != execution_sha
    ):
        raise ValueError("execution SHA does not resolve to the requested commit")
    if (
        _git_text(
            git_root,
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{execution_sha}^{{tree}}",
        )
        != expected_tree_sha
    ):
        raise ValueError("execution tree SHA mismatch")
    bindings = _qualification_bindings(
        git_root=git_root,
        execution_sha=execution_sha,
        expected_spec_sha256=expected_spec_sha256,
    )
    if _snapshot_files(source_root) != set(bindings):
        raise RuntimeError("authenticated source snapshot inventory mismatch")
    for relative, expected_blob in bindings.items():
        payload = (source_root / Path(*PurePosixPath(relative).parts)).read_bytes()
        actual_blob = _git_blob_sha(payload)
        if actual_blob != expected_blob:
            raise RuntimeError(f"authenticated source snapshot changed: {relative}")


def _run_worker(mode: str, arguments: Sequence[str]) -> None:
    known, forwarded = _split_worker_arguments(arguments)
    source_root = known.source_root.resolve(strict=True)
    git_root = known.git_root.resolve(strict=True)
    forwarded_execution_shas = [
        forwarded[index + 1]
        for index, value in enumerate(forwarded[:-1])
        if value == "--execution-sha"
    ]
    if forwarded_execution_shas != [known.execution_sha]:
        raise ValueError("worker execution SHA does not match authenticated bootstrap identity")
    canonical_git_root = Path(
        _git_text(git_root, "rev-parse", "--show-toplevel")
    ).resolve(strict=True)
    if canonical_git_root != git_root:
        raise ValueError("Git root must be the canonical repository top-level directory")
    _validate_worker_snapshot(
        source_root=source_root,
        git_root=canonical_git_root,
        execution_sha=known.execution_sha,
        expected_tree_sha=known.expected_tree_sha,
        expected_spec_sha256=known.expected_spec_sha256,
    )
    _configure_worker_paths(source_root, canonical_git_root)
    if mode == "worker-parent":
        from scripts.reading_order_post_v2_qualification import run_v3

        run_v3.main(
            [
                *forwarded,
                "--source-root",
                str(source_root),
                "--git-root",
                str(canonical_git_root),
            ]
        )
        return

    from scripts.reading_order_post_v2_qualification import run_arm_v3

    run_arm_v3.main(
        [
            *forwarded,
            "--source-root",
            str(source_root),
            "--git-root",
            str(canonical_git_root),
        ]
    )


def main(argv: Sequence[str] | None = None) -> None:
    _require_isolated_interpreter()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        raise ValueError("v3 bootstrap mode is required")
    mode, remaining = arguments[0], arguments[1:]
    if mode == "parent":
        _run_parent(remaining)
    elif mode in {"worker-parent", "arm"}:
        _run_worker(mode, remaining)
    else:
        raise ValueError(f"unsupported v3 bootstrap mode: {mode}")


if __name__ == "__main__":
    main()
