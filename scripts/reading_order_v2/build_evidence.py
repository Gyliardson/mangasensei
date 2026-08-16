from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from .canonical import canonical_jsonl_bytes, write_canonical_json
from .contracts import PAGE_IDS
from .evidence import (
    ARMS,
    sha256_path,
    validate_staging_tree,
    verify_checksums,
    write_checksums,
    write_deterministic_zip,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "assets" / "reading-order-v2" / "heldout-v1"
RAW_ROOT = REPO_ROOT / "var" / "research" / "reading-order-v2" / "raw"
SUMMARY_ROOT = REPO_ROOT / "var" / "research" / "reading-order-v2" / "summary"
SPEC_PATH = REPO_ROOT / "scripts" / "reading_order_v2" / "spec" / "experiment-spec-v1.json"

SOURCE_PATHS = (
    "backend/src/mangasensei/ocr/contracts.py",
    "backend/src/mangasensei/ocr/reading_order.py",
    "backend/src/mangasensei/ocr/diagnostics/reading_order_v2.py",
    "backend/src/mangasensei/ocr/diagnostics/reading_order_v2_contracts.py",
    "backend/src/mangasensei/ocr/vendor/manga_image_translator/manga_translator/utils/generic2.py",
    "backend/src/mangasensei/ocr/vendor/manga_image_translator/manga_translator/utils/textblock.py",
    "scripts/reading_order_v2/build_evidence.py",
    "scripts/reading_order_v2/canonical.py",
    "scripts/reading_order_v2/comparison.py",
    "scripts/reading_order_v2/contracts.py",
    "scripts/reading_order_v2/evidence.py",
    "scripts/reading_order_v2/fixtures.py",
    "scripts/reading_order_v2/panel_scoring.py",
    "scripts/reading_order_v2/run_arm.py",
    "scripts/reading_order_v2/run_heldout.py",
    "scripts/reading_order_v2/scoring.py",
    "scripts/reading_order_v2/validate_corpus.py",
    "scripts/reading_order_v2/verdict.py",
    "pyproject.toml",
    "uv.lock",
)

README = """# Reading Order v2 Held-out Qualification Evidence

This deterministic bundle was produced from one authorized Reading Order v2 held-out
qualification run. `repository.json` identifies the exact executed repository SHA and
tree. `environment.json` was captured before execution from the same frozen uv
interpreter used for the qualification. Arm artifacts use repeat 1 only after the
committed runner proved diagnostics, ordering, and score equality across all three
fresh-process repeats. The raw workflow artifact is supplementary and is not part of
this evidence ZIP.
"""


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _copy_exact(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _image_hashes(manifest: dict[str, object]) -> dict[str, str]:
    inventory = manifest.get("inventory")
    if not isinstance(inventory, list):
        raise ValueError("held-out manifest inventory must be an array")
    hashes: dict[str, str] = {}
    for item in inventory:
        if not isinstance(item, dict):
            raise ValueError("malformed held-out manifest inventory entry")
        name = item.get("file")
        digest = item.get("sha256")
        if not isinstance(name, str) or not name.startswith("images/H") or not name.endswith(".png"):
            continue
        if not isinstance(digest, str):
            raise ValueError("image manifest entry missing SHA-256")
        hashes[Path(name).stem] = digest
    if set(hashes) != set(PAGE_IDS):
        raise ValueError("held-out manifest must contain exactly H01-H16 image hashes")
    return {page_id: hashes[page_id] for page_id in PAGE_IDS}


def _region_fixtures() -> dict[str, object]:
    result: dict[str, object] = {}
    for page_id in PAGE_IDS:
        result[page_id] = _load_object(CORPUS_ROOT / "inputs" / f"{page_id}.json")
    return result


def _source_manifest(staging: Path) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for repo_path in SOURCE_PATHS:
        source = REPO_ROOT / repo_path
        if not source.is_file():
            raise FileNotFoundError(f"missing harness source: {repo_path}")
        bundle_path = f"harness/source/{repo_path}"
        destination = staging / bundle_path
        _copy_exact(source, destination)
        records.append(
            {
                "repoPath": repo_path,
                "bundlePath": bundle_path,
                "bytes": source.stat().st_size,
                "sha256": sha256_path(source),
                "gitBlobSha": _git("rev-parse", f"HEAD:{repo_path}"),
            }
        )
    return {"sources": records}


def _materialize_arm(staging: Path, arm: str) -> None:
    repeat_root = RAW_ROOT / arm / "repeat-1"
    diagnostics: list[object] = []
    ordering: list[object] = []
    for page_id in PAGE_IDS:
        diagnostics.append(_load_object(repeat_root / f"{page_id}.diagnostic.json"))
        ordering.append(_load_object(repeat_root / f"{page_id}.ordering.json"))

    arm_root = staging / "arms" / arm
    arm_root.mkdir(parents=True, exist_ok=True)
    (arm_root / "diagnostics.jsonl").write_bytes(canonical_jsonl_bytes(diagnostics))
    write_canonical_json(arm_root / "ordering.json", ordering)
    _copy_exact(SUMMARY_ROOT / arm / "scores.json", arm_root / "scores.json")
    _copy_exact(SUMMARY_ROOT / arm / "repeat-hashes.json", arm_root / "repeat-hashes.json")


def build_evidence(
    *, baseline_sha: str, environment_json: Path, staging: Path, destination_zip: Path
) -> None:
    if staging.exists():
        raise FileExistsError(f"evidence staging directory already exists: {staging.name}")
    if _git("status", "--porcelain"):
        raise RuntimeError("tracked repository tree must be clean before evidence packaging")

    executed_sha = _git("rev-parse", "HEAD")
    executed_tree = _git("rev-parse", "HEAD^{tree}")
    environment = _load_object(environment_json)
    if environment.get("executionSha") != executed_sha:
        raise ValueError("environment executionSha does not match HEAD")
    if environment.get("executionTreeSha") != executed_tree:
        raise ValueError("environment executionTreeSha does not match HEAD tree")

    staging.mkdir(parents=True)
    _copy_exact(SPEC_PATH, staging / "experiment-spec.json")
    _copy_exact(CORPUS_ROOT / "corpus-design.json", staging / "heldout" / "corpus-design.json")
    _copy_exact(CORPUS_ROOT / "manifest.json", staging / "heldout" / "manifest.json")

    write_canonical_json(
        staging / "repository.json",
        {
            "repository": "Gyliardson/mangasensei",
            "baselineRepositorySha": baseline_sha,
            "executedRepositorySha": executed_sha,
            "executedTreeSha": executed_tree,
            "dirtyTree": False,
        },
    )
    write_canonical_json(staging / "environment.json", environment)
    write_canonical_json(staging / "inputs" / "region-fixtures.json", _region_fixtures())
    manifest = _load_object(CORPUS_ROOT / "manifest.json")
    write_canonical_json(staging / "inputs" / "image-hashes.json", _image_hashes(manifest))

    source_manifest = _source_manifest(staging)
    write_canonical_json(staging / "harness" / "source-manifest.json", source_manifest)

    for arm in ARMS:
        _materialize_arm(staging, arm)

    _copy_exact(SUMMARY_ROOT / "comparison.json", staging / "comparison.json")
    _copy_exact(SUMMARY_ROOT / "verdict.json", staging / "verdict.json")
    (staging / "README.md").write_text(README, encoding="utf-8", newline="\n")

    write_checksums(staging)
    validate_staging_tree(staging)
    verify_checksums(staging)
    write_deterministic_zip(staging, destination_zip)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic Reading Order v2 evidence")
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--environment-json", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--zip-path", type=Path, required=True)
    args = parser.parse_args()
    build_evidence(
        baseline_sha=args.baseline_sha,
        environment_json=args.environment_json,
        staging=args.staging_dir,
        destination_zip=args.zip_path,
    )


if __name__ == "__main__":
    main()
