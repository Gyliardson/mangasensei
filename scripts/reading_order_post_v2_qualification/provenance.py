from __future__ import annotations

import argparse
import importlib.metadata
import locale
import os
import platform
import subprocess
import sys
from pathlib import Path
from shutil import which

from .canonical import sha256_path, write_canonical_json
from .contracts import load_corpus_design

REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for provenance")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _uv_version() -> str:
    uv = which("uv")
    if uv is None:
        raise RuntimeError("uv is required for qualification provenance")
    return subprocess.run(  # noqa: S603
        [uv, "--version"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _version(distribution: str) -> str:
    return importlib.metadata.version(distribution)


def capture(
    *,
    destination: Path,
    corpus_root: Path,
    experiment_id: str,
    qualification_identity: str,
    spec_sha256: str,
    manifest_sha256: str,
    design_sha256: str,
    command: str,
) -> None:
    design = load_corpus_design(corpus_root / "corpus-design.json")
    package_paths = ("pyproject.toml", "uv.lock")
    payload = {
        "schemaVersion": "reading-order-post-v2-actions-provenance-v1",
        "repository": "Gyliardson/mangasensei",
        "executionSha": _git("rev-parse", "HEAD"),
        "executionTreeSha": _git("rev-parse", "HEAD^{tree}"),
        "experimentId": experiment_id,
        "qualificationIdentity": qualification_identity,
        "specSha256": spec_sha256,
        "corpusId": design.corpus_id,
        "corpusVersion": design.version,
        "manifestSha256": manifest_sha256,
        "designSha256": design_sha256,
        "command": command,
        "runner": {
            "os": os.environ.get("RUNNER_OS", "unknown"),
            "arch": os.environ.get("RUNNER_ARCH", "unknown"),
            "environment": os.environ.get("RUNNER_ENVIRONMENT", "unknown"),
            "imageOs": os.environ.get("ImageOS", "unknown"),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executableBasename": Path(sys.executable).name,
            "virtualEnvironmentActive": sys.prefix != sys.base_prefix,
        },
        "uvVersion": _uv_version(),
        "packages": {
            "numpy": _version("numpy"),
            "opencv-python-headless": _version("opencv-python-headless"),
            "Pillow": _version("Pillow"),
            "shapely": _version("shapely"),
            "networkx": _version("networkx"),
            "py3langid": _version("py3langid"),
        },
        "locale": locale.getlocale(),
        "cpuLogicalCount": os.cpu_count(),
        "packageManifestIdentity": {
            path: {
                "sha256": sha256_path(REPO_ROOT / path),
                "gitBlobSha": _git("rev-parse", f"HEAD:{path}"),
            }
            for path in package_paths
        },
    }
    write_canonical_json(destination, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture post-v2 qualification provenance")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--qualification-identity", required=True)
    parser.add_argument("--spec-sha256", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--design-sha256", required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    capture(
        destination=args.destination,
        corpus_root=args.corpus_root,
        experiment_id=args.experiment_id,
        qualification_identity=args.qualification_identity,
        spec_sha256=args.spec_sha256,
        manifest_sha256=args.manifest_sha256,
        design_sha256=args.design_sha256,
        command=args.command,
    )


if __name__ == "__main__":
    main()
