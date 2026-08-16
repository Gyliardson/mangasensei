from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from . import EVIDENCE_SCHEMA_VERSION
from .canonical import (
    canonical_jsonl_bytes,
    sha256_path,
    verify_checksums,
    write_canonical_json,
    write_checksums,
    write_deterministic_zip,
)
from .contracts import ArmId, load_corpus_design
from .spec import validate_spec

REPO_ROOT = Path(__file__).resolve().parents[2]

README = """# Reading Order post-v2 held-out qualification evidence

This deterministic bundle is evidence for exactly one authorized execution of
`reading-order-post-v2-c1-c2-c3-b1-v1`. It is qualification evidence only for the
sealed corpus, spec hash, repository SHA/tree, and qualification identity recorded
inside the bundle. It is not production activation and does not authorize replay,
tuning, release, or reuse of the corpus as future held-out evidence.
"""


def _git(*args: str) -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for evidence packaging")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _source_manifest(staging: Path, spec: dict[str, Any]) -> dict[str, object]:
    paths = {
        str(spec["candidateBinding"]["sourcePath"]),
        "pyproject.toml",
        "uv.lock",
    }
    raw_bindings = spec.get("sourceBindings")
    if not isinstance(raw_bindings, list):
        raise ValueError("spec sourceBindings missing")
    for record in raw_bindings:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise ValueError("spec source binding malformed")
        paths.add(record["path"])

    records: list[dict[str, object]] = []
    for repo_path in sorted(paths):
        source = REPO_ROOT / repo_path
        if not source.is_file():
            raise FileNotFoundError(f"missing frozen source: {repo_path}")
        bundle_path = f"harness/source/{repo_path}"
        _copy(source, staging / bundle_path)
        records.append(
            {
                "repoPath": repo_path,
                "bundlePath": bundle_path,
                "bytes": source.stat().st_size,
                "sha256": sha256_path(source),
                "gitBlobSha": _git("rev-parse", f"HEAD:{repo_path}"),
            }
        )
    return {"schemaVersion": "reading-order-post-v2-source-manifest-v1", "sources": records}


def _aggregate_arm(
    *,
    output_root: Path,
    staging: Path,
    arm: ArmId,
    page_ids: tuple[str, ...],
) -> None:
    repeat_root = output_root / "raw" / arm.value / "repeat-1"
    diagnostics: list[object] = []
    ordering: list[object] = []
    for page_id in page_ids:
        diagnostics.append(_load(repeat_root / f"{page_id}.diagnostic.json"))
        ordering.append(_load(repeat_root / f"{page_id}.ordering.json"))
    arm_root = staging / "arms" / arm.value
    arm_root.mkdir(parents=True, exist_ok=True)
    (arm_root / "diagnostics.jsonl").write_bytes(canonical_jsonl_bytes(diagnostics))
    write_canonical_json(arm_root / "ordering.json", ordering)
    _copy(output_root / "summary" / arm.value / "scores.json", arm_root / "scores.json")
    _copy(
        output_root / "summary" / arm.value / "repeat-hashes.json",
        arm_root / "repeat-hashes.json",
    )


def _write_output_inventory(output_root: Path, destination: Path) -> None:
    lines: list[str] = []
    for path in sorted(item for item in output_root.rglob("*") if item.is_file()):
        relative = path.relative_to(output_root).as_posix()
        lines.append(f"{sha256_path(path)}  {relative}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def validate_staging_tree(staging: Path) -> None:
    required = {
        "README.md",
        "experiment-spec.json",
        "heldout/corpus-design.json",
        "heldout/manifest.json",
        "provenance/environment.json",
        "provenance/run-metadata.json",
        "comparison.json",
        "exercise.json",
        "repeat-hashes.json",
        "verdict.json",
        "harness/source-manifest.json",
        "output-inventory.sha256",
        "checksums.sha256",
    }
    existing = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
    }
    missing = sorted(required - existing)
    if missing:
        raise ValueError(f"evidence staging missing required files: {missing}")
    for arm in ArmId:
        for name in ("diagnostics.jsonl", "ordering.json", "scores.json", "repeat-hashes.json"):
            relative = f"arms/{arm.value}/{name}"
            if relative not in existing:
                raise ValueError(f"evidence staging missing {relative}")


def build_evidence(
    *,
    spec_path: Path,
    expected_spec_sha256: str,
    corpus_root: Path,
    environment_json: Path,
    output_root: Path,
    staging: Path,
    destination_zip: Path,
) -> None:
    if staging.exists():
        raise FileExistsError("evidence staging directory must not already exist")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("tracked repository must be clean before evidence packaging")

    spec = validate_spec(spec_path, expected_sha256=expected_spec_sha256)
    design = load_corpus_design(corpus_root / "corpus-design.json")
    environment = _load(environment_json)
    run_metadata = _load(output_root / "summary" / "run-metadata.json")
    if environment.get("executionSha") != _git("rev-parse", "HEAD"):
        raise ValueError("environment execution SHA does not match HEAD")
    if environment.get("executionTreeSha") != _git("rev-parse", "HEAD^{tree}"):
        raise ValueError("environment execution tree does not match HEAD")
    if run_metadata.get("executionSha") != environment.get("executionSha"):
        raise ValueError("runner/environment execution SHA mismatch")

    staging.mkdir(parents=True)
    _copy(spec_path, staging / "experiment-spec.json")
    _copy(corpus_root / "corpus-design.json", staging / "heldout" / "corpus-design.json")
    _copy(corpus_root / "manifest.json", staging / "heldout" / "manifest.json")
    _copy(environment_json, staging / "provenance" / "environment.json")
    _copy(
        output_root / "summary" / "run-metadata.json",
        staging / "provenance" / "run-metadata.json",
    )

    write_canonical_json(
        staging / "evidence-metadata.json",
        {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "experimentId": spec["experimentId"],
            "qualificationIdentity": run_metadata["qualificationIdentity"],
            "classification": "NEW_FROZEN_HELDOUT_QUALIFICATION_NOT_PRODUCTION_ACTIVATION",
        },
    )
    write_canonical_json(
        staging / "harness" / "source-manifest.json", _source_manifest(staging, spec)
    )
    for arm in ArmId:
        _aggregate_arm(
            output_root=output_root,
            staging=staging,
            arm=arm,
            page_ids=design.page_ids,
        )

    for name in ("comparison.json", "exercise.json", "repeat-hashes.json", "verdict.json"):
        _copy(output_root / "summary" / name, staging / name)
    _write_output_inventory(output_root, staging / "output-inventory.sha256")
    (staging / "README.md").write_text(README, encoding="utf-8", newline="\n")

    write_checksums(staging)
    validate_staging_tree(staging)
    verify_checksums(staging)
    write_deterministic_zip(staging, destination_zip)
