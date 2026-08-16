from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from . import (
    ANNOTATION_SCHEMA_VERSION,
    CORPUS_DESIGN_SCHEMA_VERSION,
    CORPUS_MANIFEST_SCHEMA_VERSION,
    DIAGNOSTIC_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    EXPERIMENT_ID,
    INPUT_SCHEMA_VERSION,
    SPEC_SCHEMA_VERSION,
)
from .canonical import canonical_json_bytes, sha256_bytes
from .contracts import (
    ArmId,
    DESIGN_REQUIREMENTS,
    EXERCISE_MINIMA,
    LAYOUT_TAG_MINIMA,
    REQUIRED_SLICES,
    SLICE_MINIMA,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


class SpecError(ValueError):
    pass


def _git(*args: str) -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for frozen source identity validation")
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
        raise SpecError("experiment spec must be a JSON object")
    return value


def canonical_spec_sha256(path: Path) -> str:
    return sha256_bytes(canonical_json_bytes(_load(path)))


def validate_spec(path: Path, *, expected_sha256: str | None = None) -> dict[str, Any]:
    spec = _load(path)
    if (
        expected_sha256 is not None
        and sha256_bytes(canonical_json_bytes(spec)) != expected_sha256
    ):
        raise SpecError("canonical experiment spec SHA-256 mismatch")
    if spec.get("schemaVersion") != SPEC_SCHEMA_VERSION:
        raise SpecError("wrong experiment spec schema version")
    if spec.get("experimentId") != EXPERIMENT_ID:
        raise SpecError("wrong experiment identity")
    if spec.get("repository") != "Gyliardson/mangasensei":
        raise SpecError("wrong canonical repository")
    if spec.get("status") != "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION":
        raise SpecError("experiment spec status changed")
    if spec.get("diagnosticSchemaVersion") != DIAGNOSTIC_SCHEMA_VERSION:
        raise SpecError("diagnostic schema identity changed")
    if spec.get("evidenceBundleSchemaVersion") != EVIDENCE_SCHEMA_VERSION:
        raise SpecError("evidence schema identity changed")
    if spec.get("corpusSchemas") != {
        "design": CORPUS_DESIGN_SCHEMA_VERSION,
        "manifest": CORPUS_MANIFEST_SCHEMA_VERSION,
        "input": INPUT_SCHEMA_VERSION,
        "annotation": ANNOTATION_SCHEMA_VERSION,
    }:
        raise SpecError("corpus schema identities changed")
    if spec.get("arms") != [arm.value for arm in ArmId]:
        raise SpecError("frozen arm set changed")
    if spec.get("requiredSlices") != sorted(REQUIRED_SLICES):
        raise SpecError("frozen required slices changed")
    if spec.get("corpusDesignRequirements") != DESIGN_REQUIREMENTS:
        raise SpecError("frozen corpus minima changed")
    if spec.get("sliceMinima") != SLICE_MINIMA:
        raise SpecError("frozen slice minima changed")
    if spec.get("layoutTagMinima") != LAYOUT_TAG_MINIMA:
        raise SpecError("frozen layout-tag minima changed")
    if spec.get("exerciseMinima") != EXERCISE_MINIMA:
        raise SpecError("frozen exercise minima changed")

    candidate = spec.get("candidateBinding")
    if not isinstance(candidate, dict):
        raise SpecError("candidate binding missing")
    required_candidate = {"commitSha", "treeSha", "sourcePath", "sourceBlobSha"}
    if set(candidate) != required_candidate:
        raise SpecError("candidate binding shape changed")
    commit_sha, tree_sha = candidate["commitSha"], candidate["treeSha"]
    source_path, source_blob = candidate["sourcePath"], candidate["sourceBlobSha"]
    if not all(isinstance(item, str) for item in (commit_sha, tree_sha, source_path, source_blob)):
        raise SpecError("candidate binding values must be strings")
    if HEX40_RE.fullmatch(commit_sha) is None or HEX40_RE.fullmatch(tree_sha) is None:
        raise SpecError("candidate commit/tree SHA malformed")
    if _git("rev-parse", f"{commit_sha}^{{tree}}") != tree_sha:
        raise SpecError("candidate frozen commit/tree binding mismatch")
    if _git("rev-parse", f"{commit_sha}:{source_path}") != source_blob:
        raise SpecError("candidate frozen commit/source blob binding mismatch")
    if _git("rev-parse", f"HEAD:{source_path}") != source_blob:
        raise SpecError("current execution tree changed frozen candidate source")

    source_bindings = spec.get("sourceBindings")
    if not isinstance(source_bindings, list) or not source_bindings:
        raise SpecError("source bindings missing")
    seen_paths: set[str] = set()
    for index, record in enumerate(source_bindings):
        if not isinstance(record, dict) or set(record) != {"role", "path", "gitBlobSha"}:
            raise SpecError(f"sourceBindings[{index}] malformed")
        role, repo_path, blob = record["role"], record["path"], record["gitBlobSha"]
        if not all(isinstance(item, str) and item for item in (role, repo_path, blob)):
            raise SpecError(f"sourceBindings[{index}] invalid")
        if repo_path in seen_paths:
            raise SpecError("duplicate source binding")
        seen_paths.add(repo_path)
        if _git("rev-parse", f"HEAD:{repo_path}") != blob:
            raise SpecError(f"frozen source binding changed: {repo_path}")

    return spec
