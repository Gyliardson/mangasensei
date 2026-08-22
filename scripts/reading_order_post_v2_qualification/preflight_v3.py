from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any

from scripts.reading_order_v3_authoring.validate import (
    validate_corpus as validate_authoring_corpus,
)

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


@dataclass(frozen=True, slots=True)
class ValidatedV3Context:
    experiment_id: str
    qualification_identity: str
    execution_sha: str
    execution_tree_sha: str
    spec: dict[str, Any]
    coverage: object


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


def _validate_runtime_candidate(spec: dict[str, Any]) -> None:
    binding = spec.get("candidateBinding")
    if not isinstance(binding, dict) or not isinstance(binding.get("path"), str):
        raise RuntimeError("frozen v3 candidate path binding is missing")
    candidate_repo_path = binding["path"]
    expected_origin = (REPO_ROOT / candidate_repo_path).resolve(strict=True)
    raw_origin = getattr(runtime_candidate_module, "__file__", None)
    if not isinstance(raw_origin, (str, Path)):
        raise RuntimeError("runtime candidate module origin is missing")
    try:
        runtime_origin = Path(raw_origin).resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("runtime candidate module origin cannot be resolved") from exc
    if runtime_origin != expected_origin:
        raise RuntimeError("runtime candidate module origin does not match frozen candidate path")
    if getattr(exercise_v3, "candidate_module", None) is not runtime_candidate_module:
        raise RuntimeError("exercise_v3 candidate must be the same module object")

    runtime_bytes = runtime_origin.read_bytes()
    head_bytes = _git_bytes("show", f"HEAD:{candidate_repo_path}")
    runtime_sha256 = hashlib.sha256(runtime_bytes).digest()
    head_sha256 = hashlib.sha256(head_bytes).digest()
    if not hmac.compare_digest(runtime_sha256, head_sha256) or runtime_bytes != head_bytes:
        raise RuntimeError("runtime candidate bytes do not match HEAD-bound candidate bytes")


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

    assert_no_historical_v2_content_reuse(corpus_root)
    assert_no_retired_post_v2_v1_reuse(corpus_root)
    coverage = validate_authoring_corpus(corpus_root)
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
        coverage=coverage,
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
