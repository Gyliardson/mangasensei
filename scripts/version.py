"""Keep MangaSensei release-version mirrors synchronized.

The Python project version in ``pyproject.toml`` is authoritative. ``set`` delegates
that update to ``uv version`` so ``uv.lock`` is updated by uv itself, then updates
the small number of non-Python mirrors. CHANGELOG.md is intentionally editorial
and is never modified automatically.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = data["project"]["version"]
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise ValueError(f"project version must be stable SemVer (X.Y.Z), got {version!r}")
    return version


def _json(path: str) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _extract(pattern: str, path: str) -> str:
    text = (ROOT / path).read_text(encoding="utf-8")
    match = re.search(pattern, text, flags=re.MULTILINE)
    if match is None:
        raise ValueError(f"expected version marker not found in {path}")
    return match.group(1)


def version_mirrors() -> dict[str, str]:
    package = _json("package.json")
    frontend = _json("frontend/package.json")
    package_lock = _json("package-lock.json")
    lock_packages = package_lock.get("packages")
    if not isinstance(lock_packages, dict):
        raise ValueError("package-lock.json is missing packages")
    root_lock = lock_packages.get("")
    frontend_lock = lock_packages.get("frontend")
    if not isinstance(root_lock, dict) or not isinstance(frontend_lock, dict):
        raise ValueError("package-lock.json is missing workspace package metadata")

    return {
        "VERSION": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "backend/src/mangasensei/__init__.py": _extract(
            r'^__version__\s*=\s*"([^"]+)"$', "backend/src/mangasensei/__init__.py"
        ),
        "package.json": str(package.get("version", "")),
        "frontend/package.json": str(frontend.get("version", "")),
        "package-lock.json": str(package_lock.get("version", "")),
        'package-lock.json packages[""]': str(root_lock.get("version", "")),
        'package-lock.json packages["frontend"]': str(frontend_lock.get("version", "")),
    }


def check() -> int:
    expected = project_version()
    mismatches = {
        location: actual
        for location, actual in version_mirrors().items()
        if actual != expected
    }
    if mismatches:
        print(f"Expected all version mirrors to equal {expected}:", file=sys.stderr)
        for location, actual in mismatches.items():
            print(f"  {location}: {actual or '<missing>'}", file=sys.stderr)
        print(
            "Run `python scripts/version.py set X.Y.Z` instead of editing mirrors manually.",
            file=sys.stderr,
        )
        return 1

    print(f"Version mirrors are consistent: {expected}")
    return 0


def _write_json(path: str, data: dict[str, Any]) -> None:
    (ROOT / path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _replace(pattern: str, replacement: str, path: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f"expected exactly one version marker in {path}, found {count}")
    target.write_text(updated, encoding="utf-8")


def set_version(new_version: str) -> int:
    if not SEMVER.fullmatch(new_version):
        raise ValueError("release version must use stable SemVer X.Y.Z")

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv executable not found")
    # new_version is constrained to X.Y.Z above and shell=False; no command injection is possible.
    subprocess.run(  # noqa: S603
        [uv, "version", new_version, "--no-sync"],
        cwd=ROOT,
        check=True,
    )
    (ROOT / "VERSION").write_text(f"{new_version}\n", encoding="utf-8")
    _replace(
        r'^__version__\s*=\s*"[^"]+"$',
        f'__version__ = "{new_version}"',
        "backend/src/mangasensei/__init__.py",
    )

    package = _json("package.json")
    package["version"] = new_version
    _write_json("package.json", package)

    frontend = _json("frontend/package.json")
    frontend["version"] = new_version
    _write_json("frontend/package.json", frontend)

    package_lock = _json("package-lock.json")
    package_lock["version"] = new_version
    lock_packages = package_lock["packages"]
    if not isinstance(lock_packages, dict):
        raise ValueError("package-lock.json is missing packages")
    root_lock = lock_packages.get("")
    frontend_lock = lock_packages.get("frontend")
    if not isinstance(root_lock, dict) or not isinstance(frontend_lock, dict):
        raise ValueError("package-lock.json is missing workspace package metadata")
    root_lock["version"] = new_version
    frontend_lock["version"] = new_version
    _write_json("package-lock.json", package_lock)

    print(
        "Version synchronized. Update CHANGELOG.md manually with a curated release entry "
        "before tagging."
    )
    return check()


def release_notes() -> int:
    version = project_version()
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(?P<body>.*?)(?=^## \[|\Z)",
        flags=re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(changelog)
    if match is None:
        print(
            f"CHANGELOG.md has no release section for {version}; promote [Unreleased] first.",
            file=sys.stderr,
        )
        return 1
    body = match.group("body").strip()
    if not body:
        print(f"CHANGELOG.md release section for {version} is empty.", file=sys.stderr)
        return 1
    print(body)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("check", help="verify all non-editorial version mirrors")
    set_parser = subcommands.add_parser("set", help="set and synchronize a stable release version")
    set_parser.add_argument("version")
    subcommands.add_parser(
        "release-notes", help="print the curated CHANGELOG section for the current version"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return check()
        if args.command == "set":
            return set_version(args.version)
        if args.command == "release-notes":
            return release_notes()
        raise AssertionError(f"unhandled command: {args.command}")
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"version tooling failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
