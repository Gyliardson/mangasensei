from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import lzma
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from scripts.reading_order_post_v2_qualification import bootstrap_v3

REPO_ROOT = Path(__file__).resolve().parents[2]


def _trusted_tool(name: str) -> Path | None:
    executable = which(name)
    if executable is None:
        return None
    resolved = Path(executable).resolve(strict=True)
    forbidden_roots = (REPO_ROOT.resolve(strict=True), Path(sys.prefix).resolve(strict=True))
    if any(resolved.is_relative_to(root) for root in forbidden_roots):
        raise AssertionError(f"trusted {name} executable cannot come from the checkout runtime")
    return resolved


GIT = _trusted_tool("git")
assert GIT is not None

HISTORICAL_OBJECT_CAPSULE = Path(__file__).with_name("historical_git_objects.xz.b64")
RUNTIME_DISTRIBUTION_NAMES = (
    "numpy",
    "Pillow",
    "opencv-python-headless",
    "Shapely",
    "py3langid",
)
CAPSULE_OBJECT_TYPES = {
    "11ee6274b7abb4ab9fc386257fbf6fbdd690a85c": "tree",
    "122f575c1c3567787aec29da0b1996fe0bf3e110": "blob",
    "12358a59deee7bd0ec0845963da1b98f031592f1": "blob",
    "292f0a8c8142d919ac4184159d102789c43b4116": "commit",
    "2ef000dd57f1d866ea5cf7d771129ea610cd8e55": "tree",
    "6605f6de429b318139fb91a4535ebbd2193508ce": "tree",
    "68418482b8ccf5d7a3cb1c9ef3834505bd20cd4c": "tree",
    "7ec9ffb049f1398aa83d6feaaf7de5a2ffb6c9a1": "tree",
    "82c79db04428fddf238ab86e66c31b4fcdf84b6f": "tree",
    "88df025be214c9b631d1f57df884189777bcc0b7": "tree",
    "a666e4729004b023b4fcfc2ec6cbd2584adc2820": "tree",
    "aaa5d5ba6e8d1ac9d369b0d9fcc1fb8aee5b375f": "tree",
    "c8c2c5640ca948fde3d6f173f384e89e2c194b07": "tree",
    "ed1be14f4ad47c317ad755b94f1b3e23e84064da": "blob",
    "f20b7ddd3f9e1d44c7795c8fef51994d1a3c457f": "tree",
    "f45facb2284d740df2f294800f705414e0ba465e": "commit",
}


@dataclass(frozen=True, slots=True)
class ExecutionRepository:
    root: Path
    execution_sha: str
    tree_sha: str
    spec_sha256: str


def provenance_subprocess_environment(
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(
            ("GIT_", "PYTHON", "DYLD_", "LD_", "_RLD_")
        )
        and key.upper() != "VIRTUAL_ENV"
    }
    existing_path = environment.get("PATH", "")
    environment.update(
        {
            "GIT_CONFIG_COUNT": "0",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.pathsep.join((str(GIT.parent), existing_path)),
        }
    )
    if overrides is not None:
        if any(
            key.upper().startswith(("GIT_", "DYLD_", "LD_", "_RLD_"))
            for key in overrides
        ):
            raise AssertionError("provenance environment overrides cannot restore tool injection")
        environment.update(overrides)
    return environment


def _git(
    repository: Path,
    *args: str,
    input_bytes: bytes | None = None,
) -> bytes:
    return subprocess.run(  # noqa: S603
        [GIT, "--no-replace-objects", "-C", str(repository), *args],
        input=input_bytes,
        check=True,
        capture_output=True,
        env=provenance_subprocess_environment(),
    ).stdout


def _git_text(
    repository: Path, *args: str, input_bytes: bytes | None = None
) -> str:
    return _git(repository, *args, input_bytes=input_bytes).decode("utf-8").strip()


def _capsule_records() -> dict[str, dict[str, str]]:
    encoded = "".join(HISTORICAL_OBJECT_CAPSULE.read_text(encoding="ascii").splitlines())
    raw = lzma.decompress(base64.b64decode(encoded, validate=True))
    value: object = json.loads(raw)
    if not isinstance(value, dict):
        raise AssertionError("historical Git object capsule must contain an object map")
    records: dict[str, dict[str, str]] = {}
    for oid, record in value.items():
        if (
            not isinstance(oid, str)
            or not isinstance(record, dict)
            or set(record) != {"type", "payload"}
            or not all(isinstance(item, str) for item in record.values())
        ):
            raise AssertionError("historical Git object capsule record is malformed")
        records[oid] = {str(key): str(item) for key, item in record.items()}
    return records


def _install_historical_objects(repository: Path) -> None:
    records = _capsule_records()
    if {oid: record["type"] for oid, record in records.items()} != CAPSULE_OBJECT_TYPES:
        raise AssertionError("historical Git object capsule inventory changed")
    for expected_oid, record in records.items():
        payload = base64.b64decode(record["payload"], validate=True)
        header = f'{record["type"]} {len(payload)}\0'.encode("ascii")
        independent_oid = hashlib.sha1(  # noqa: S324 - Git SHA-1 object identity
            header + payload, usedforsecurity=False
        ).hexdigest()
        if independent_oid != expected_oid:
            raise AssertionError(f"historical Git object payload changed: {expected_oid}")
        actual_oid = _git_text(
            repository,
            "hash-object",
            "-w",
            "-t",
            record["type"],
            "--stdin",
            input_bytes=payload,
        )
        if actual_oid != expected_oid:
            raise AssertionError(f"historical Git object payload changed: {expected_oid}")


def build_execution_repository(destination: Path) -> ExecutionRepository:
    reviewed_head = _git_text(REPO_ROOT, "rev-parse", "HEAD")
    reviewed_tree_sha = _git_text(REPO_ROOT, "rev-parse", "HEAD^{tree}")
    subprocess.run(  # noqa: S603
        [
            GIT,
            "clone",
            "--no-local",
            "--no-hardlinks",
            "--depth=1",
            "--no-tags",
            "--template=",
            str(REPO_ROOT),
            str(destination),
        ],
        check=True,
        capture_output=True,
        env=provenance_subprocess_environment(),
    )
    if (destination / ".git" / "objects" / "info" / "alternates").exists():
        raise AssertionError("synthetic execution repository cannot share Git objects")
    _git(destination, "config", "core.autocrlf", "false")
    _install_historical_objects(destination)
    execution_sha = _git_text(
        destination,
        "-c",
        "user.name=Qualification Test",
        "-c",
        "user.email=qualification@example.invalid",
        "commit-tree",
        reviewed_tree_sha,
        "-p",
        reviewed_head,
        "-m",
        "synthetic authenticated execution",
    )
    _git(destination, "update-ref", "HEAD", execution_sha, reviewed_head)
    tree_sha = _git_text(destination, "rev-parse", "HEAD^{tree}")
    if tree_sha != reviewed_tree_sha:
        raise AssertionError("synthetic execution tree differs from reviewed checkout HEAD tree")
    spec_path = destination / bootstrap_v3.SPEC_REPO_PATH
    spec_sha256 = hashlib.sha256(spec_path.read_bytes()).hexdigest()
    return ExecutionRepository(destination, execution_sha, tree_sha, spec_sha256)


def _normalized_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _locked_runtime_distributions() -> dict[str, tuple[str, tuple[str, ...]]]:
    project = tomllib.loads(_git(REPO_ROOT, "show", "HEAD:pyproject.toml").decode("utf-8"))
    lock = tomllib.loads(_git(REPO_ROOT, "show", "HEAD:uv.lock").decode("utf-8"))
    dependencies = [
        *project["project"]["dependencies"],
        *project["project"]["optional-dependencies"]["ocr"],
    ]
    exact_versions = {
        _normalized_distribution_name(name): version
        for dependency in dependencies
        if "==" in dependency
        for name, version in [dependency.split("==", maxsplit=1)]
    }
    locked_packages = {
        _normalized_distribution_name(package["name"]): package
        for package in lock["package"]
        if isinstance(package, dict) and isinstance(package.get("name"), str)
    }
    locked: dict[str, tuple[str, tuple[str, ...]]] = {}
    for name in RUNTIME_DISTRIBUTION_NAMES:
        version = exact_versions.get(_normalized_distribution_name(name))
        if version is None:
            raise AssertionError(f"runtime distribution is not exactly pinned: {name}")
        package = locked_packages.get(_normalized_distribution_name(name))
        if package is None or package.get("version") != version:
            raise AssertionError(f"runtime distribution lock entry changed: {name}")
        wheels = package.get("wheels")
        if not isinstance(wheels, list):
            raise AssertionError(f"runtime distribution has no locked wheels: {name}")
        hashes = tuple(
            wheel["hash"]
            for wheel in wheels
            if isinstance(wheel, dict)
            and isinstance(wheel.get("hash"), str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", wheel["hash"])
        )
        if not hashes or len(hashes) != len(wheels):
            raise AssertionError(f"runtime distribution wheel hashes are incomplete: {name}")
        locked[name] = (version, hashes)
    return locked


def _locked_distribution(
    name: str, expected_version: str, source_root: Path
) -> importlib.metadata.Distribution:
    normalized = _normalized_distribution_name(name)
    matches = [
        distribution
        for distribution in importlib.metadata.distributions(path=[str(source_root)])
        if isinstance(distribution.metadata["Name"], str)
        and _normalized_distribution_name(distribution.metadata["Name"]) == normalized
    ]
    if len(matches) != 1:
        raise AssertionError(f"locked distribution resolution is ambiguous: {name}")
    distribution = matches[0]
    if distribution.version != expected_version:
        raise AssertionError(
            f"locked distribution version changed: {name} {distribution.version}"
        )
    return distribution


def _copy_distribution(
    name: str,
    expected_version: str,
    *,
    source_root: Path,
    destination: Path,
) -> None:
    distribution = _locked_distribution(name, expected_version, source_root)
    files = distribution.files
    if files is None:
        raise AssertionError(f"locked distribution has no installed-file ledger: {name}")
    copied = 0
    for file in sorted(files, key=str):
        unresolved_source = Path(distribution.locate_file(file))
        lexical_source = Path(os.path.abspath(unresolved_source))
        source = lexical_source.resolve(strict=False)
        if not source.is_relative_to(source_root):
            raise AssertionError(f"cached distribution path escapes archive root: {name}")
        lexical_relative = lexical_source.relative_to(source_root)
        lexical_parts = lexical_relative.parts
        if any(
            source_root.joinpath(*lexical_parts[:depth]).is_symlink()
            for depth in range(1, len(lexical_parts) + 1)
        ):
            raise AssertionError(f"locked distribution contains a symlink: {name}")
        if not source.is_file():
            raise AssertionError(f"locked distribution file is unavailable: {name}/{file}")
        if file.size is not None and source.stat().st_size != file.size:
            raise AssertionError(f"locked distribution file size changed: {name}/{file}")
        if file.hash is not None:
            digest = hashlib.new(file.hash.mode, source.read_bytes()).digest()
            actual_hash = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            if actual_hash != file.hash.value:
                raise AssertionError(f"locked distribution file hash changed: {name}/{file}")
        relative = source.relative_to(source_root)
        target = destination / relative
        if target.exists():
            raise AssertionError(f"locked distribution file collision: {name}/{relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
    if copied == 0:
        raise AssertionError(f"locked distribution has no copied site-packages files: {name}")


def _uv_cache_root() -> Path:
    configured = os.environ.get("UV_CACHE_DIR")
    candidates = [
        *((Path(configured),) if configured else ()),
        Path(os.environ.get("LOCALAPPDATA", "")) / "uv" / "cache",
        Path.home() / ".cache" / "uv",
        Path.home() / "Library" / "Caches" / "uv",
    ]
    roots = [candidate.resolve(strict=True) for candidate in candidates if candidate.is_dir()]
    if len(set(roots)) != 1:
        raise AssertionError("uv cache root is unavailable or ambiguous")
    root = roots[0]
    if root.is_relative_to(REPO_ROOT) or root.is_relative_to(Path(sys.prefix).resolve()):
        raise AssertionError("uv cache cannot come from the mutable checkout runtime")
    return root


def _cached_distribution_root(name: str, version: str) -> Path:
    cache_root = _uv_cache_root()
    package = _normalized_distribution_name(name)
    pointers = [
        path
        for path in cache_root.glob(f"wheels-v*/pypi/{package}/{version}-*")
        if path.is_file() and path.suffix not in {".http", ".lock", ".msgpack"}
    ]
    if len(pointers) != 1 or pointers[0].is_symlink():
        raise AssertionError(f"locked uv wheel cache entry is unavailable: {name}")
    relative = pointers[0].read_text(encoding="ascii").strip()
    if re.fullmatch(r"archive-v0/[A-Za-z0-9_-]+", relative) is None:
        raise AssertionError(f"locked uv wheel cache pointer is malformed: {name}")
    archive_root = (cache_root / relative).resolve(strict=True)
    if not archive_root.is_relative_to(cache_root) or not archive_root.is_dir():
        raise AssertionError(f"locked uv wheel cache path escapes its root: {name}")
    return archive_root


def _install_from_external_cache(
    *,
    locked_distributions: dict[str, tuple[str, tuple[str, ...]]],
    site_packages: Path,
) -> None:
    for name, (version, _hashes) in locked_distributions.items():
        _copy_distribution(
            name,
            version,
            source_root=_cached_distribution_root(name, version),
            destination=site_packages,
        )


def _install_with_uv(
    *,
    uv: Path,
    runtime_python: Path,
    destination: Path,
    locked_distributions: dict[str, tuple[str, tuple[str, ...]]],
) -> None:
    requirements = destination / "locked-runtime-requirements.txt"
    requirements.write_text(
        "\n".join(
            f'{name}=={version} {" ".join(f"--hash={value}" for value in hashes)}'
            for name, (version, hashes) in locked_distributions.items()
        )
        + "\n",
        encoding="ascii",
    )
    try:
        subprocess.run(  # noqa: S603
            [
                str(uv),
                "pip",
                "install",
                "--offline",
                "--no-deps",
                "--require-hashes",
                "--python",
                str(runtime_python),
                "--requirements",
                str(requirements),
            ],
            check=True,
            capture_output=True,
            env=provenance_subprocess_environment(),
        )
    finally:
        requirements.unlink(missing_ok=True)


def build_external_runtime(destination: Path) -> Path:
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-I",
            "-S",
            "-m",
            "venv",
            "--copies",
            "--without-pip",
            str(destination),
        ],
        check=True,
        capture_output=True,
        env=provenance_subprocess_environment(),
    )
    runtime_python = (
        destination / "Scripts" / "python.exe"
        if os.name == "nt"
        else destination / "bin" / "python"
    )
    runtime_python = runtime_python.resolve(strict=True)
    if not runtime_python.is_relative_to(destination.resolve(strict=True)):
        raise AssertionError("external test runtime interpreter must be physically copied")
    if destination.resolve(strict=True).is_relative_to(REPO_ROOT):
        raise AssertionError("external test runtime must be outside the mutable checkout")
    site_packages = (
        destination / "Lib" / "site-packages"
        if os.name == "nt"
        else destination
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    site_packages.mkdir(parents=True, exist_ok=True)
    locked_distributions = _locked_runtime_distributions()
    uv = _trusted_tool("uv")
    if uv is not None:
        _install_with_uv(
            uv=uv,
            runtime_python=runtime_python,
            destination=destination,
            locked_distributions=locked_distributions,
        )
    else:
        if os.environ.get("CI"):
            raise AssertionError("CI provenance integration requires the trusted uv executable")
        _install_from_external_cache(
            locked_distributions=locked_distributions,
            site_packages=site_packages,
        )

    probe = (
        "import importlib.metadata,sys;"
        f"sys.path.insert(0, {str(site_packages)!r});"
        "import cv2,numpy,PIL,py3langid,shapely;"
        "from pathlib import Path;"
        f"root=Path({str(destination)!r}).resolve();"
        "mods=(cv2,numpy,PIL,py3langid,shapely);"
        "assert all(Path(m.__file__).resolve().is_relative_to(root) for m in mods);"
        f"expected={dict((n,v[0]) for n,v in locked_distributions.items())!r};"
        "assert all(importlib.metadata.version(n)==v for n,v in expected.items())"
    )
    subprocess.run(  # noqa: S603
        [str(runtime_python), "-I", "-S", "-c", probe],
        check=True,
        capture_output=True,
        env=provenance_subprocess_environment(),
    )
    return runtime_python


def move_mutable_head(repository: Path, marker: Path) -> str:
    candidate = repository / (
        "backend/src/mangasensei/ocr/diagnostics/reading_order_post_v2_calibration.py"
    )
    candidate.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('mutable HEAD ran')\n",
        encoding="utf-8",
    )
    _git(repository, "add", str(candidate.relative_to(repository)))
    _git(
        repository,
        "-c",
        "user.name=Qualification Test",
        "-c",
        "user.email=qualification@example.invalid",
        "commit",
        "-m",
        "move mutable HEAD after authorization",
    )
    return _git_text(repository, "rev-parse", "HEAD")


def parent_arguments(
    *,
    execution: ExecutionRepository,
    corpus_root: Path,
    output_root: Path,
    methodology_sha256: str,
    manifest_sha256: str,
    design_sha256: str,
    qualification_identity: str,
) -> list[str]:
    return [
        "parent",
        "--git-root",
        str(execution.root),
        "--execution-sha",
        execution.execution_sha,
        "--expected-tree-sha",
        execution.tree_sha,
        "--expected-spec-sha256",
        execution.spec_sha256,
        "--corpus-root",
        str(corpus_root),
        "--experiment-spec",
        str(execution.root / bootstrap_v3.SPEC_REPO_PATH),
        "--experiment-id",
        "reading-order-post-v2-c1-c2-c3-b1-v3",
        "--expected-methodology-sha256",
        methodology_sha256,
        "--expected-manifest-sha256",
        manifest_sha256,
        "--expected-design-sha256",
        design_sha256,
        "--qualification-identity",
        qualification_identity,
        "--output-root",
        str(output_root),
    ]
