from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from shutil import which

from .canonical import sha256_path
from .contracts import validate_corpus, validate_qualification_identity
from .historical_guard import assert_no_historical_v2_content_reuse
from .png_integrity import validate_corpus_image_integrity
from .retired_guard import assert_no_retired_post_v2_v1_reuse
from .spec import REPO_ROOT, validate_spec


def _git(*args: str) -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for qualification preflight")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def validate_preflight(
    *,
    corpus_root: Path,
    spec_path: Path,
    experiment_id: str,
    expected_spec_sha256: str,
    expected_manifest_sha256: str,
    expected_design_sha256: str,
    qualification_identity: str,
    execution_sha: str,
    expected_tree_sha: str,
) -> None:
    if _git("rev-parse", "HEAD") != execution_sha:
        raise ValueError("execution SHA does not match checked-out HEAD")
    if _git("rev-parse", "HEAD^{tree}") != expected_tree_sha:
        raise ValueError("execution tree SHA mismatch")
    if _git("status", "--porcelain"):
        raise RuntimeError("preflight requires a clean repository")
    spec = validate_spec(spec_path, expected_sha256=expected_spec_sha256)
    if spec["experimentId"] != experiment_id:
        raise ValueError("dispatch experiment identity does not match frozen spec")
    manifest = corpus_root / "manifest.json"
    design = corpus_root / "corpus-design.json"
    if sha256_path(manifest) != expected_manifest_sha256:
        raise ValueError("frozen corpus manifest hash mismatch")
    if sha256_path(design) != expected_design_sha256:
        raise ValueError("frozen corpus design hash mismatch")
    assert_no_historical_v2_content_reuse(corpus_root)
    assert_no_retired_post_v2_v1_reuse(corpus_root)
    loaded_design, loaded_manifest, _ = validate_corpus(corpus_root)
    validate_corpus_image_integrity(corpus_root, loaded_design.page_ids)
    if loaded_manifest.design_sha256 != expected_design_sha256:
        raise ValueError("manifest does not bind frozen design hash")
    validate_qualification_identity(
        qualification_identity,
        experiment_id=experiment_id,
        spec_sha256=expected_spec_sha256,
        manifest_sha256=expected_manifest_sha256,
        design_sha256=expected_design_sha256,
        execution_sha=execution_sha,
        execution_tree_sha=expected_tree_sha,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fail-closed preflight for post-v2 qualification")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--expected-design-sha256", required=True)
    parser.add_argument("--qualification-identity", required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--expected-tree-sha", required=True)
    args = parser.parse_args()
    validate_preflight(
        corpus_root=args.corpus_root,
        spec_path=args.experiment_spec,
        experiment_id=args.experiment_id,
        expected_spec_sha256=args.expected_spec_sha256,
        expected_manifest_sha256=args.expected_manifest_sha256,
        expected_design_sha256=args.expected_design_sha256,
        qualification_identity=args.qualification_identity,
        execution_sha=args.execution_sha,
        expected_tree_sha=args.expected_tree_sha,
    )


if __name__ == "__main__":
    main()
