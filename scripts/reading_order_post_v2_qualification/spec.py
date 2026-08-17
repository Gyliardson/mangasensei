from __future__ import annotations

import copy
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
from .canonical import sha256_path
from .contracts import DESIGN_REQUIREMENTS, EXERCISE_MINIMA, REQUIRED_SLICES, SLICE_MINIMA, ArmId

REPO_ROOT = Path(__file__).resolve().parents[2]
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")

LEGACY_SPEC_REPO_PATH = "scripts/reading_order_post_v2_qualification/spec/experiment-spec-v1.json"
LEGACY_SPEC_PATH = REPO_ROOT / LEGACY_SPEC_REPO_PATH
LEGACY_SPEC_SCHEMA_VERSION = "reading-order-post-v2-experiment-spec-v1"
LEGACY_EXPERIMENT_ID = "reading-order-post-v2-c1-c2-c3-b1-v1"
LEGACY_SPEC_SHA256 = "1078c23cc99b3298bb77ec4a1deb452ec417f672a33479f5c115435c438bbc35"
LEGACY_SPEC_GIT_BLOB = "16e0a6b49cd2c351e3c76241e51beb52d9ce2591"

AUTHORING_BOUNDARY_V2 = (
    "Requirements only are frozen here. This repository change contains no future held-out "
    "pages, images, coordinates, ground truth, expected outputs, or fake future hashes. Future "
    "authoring must be isolated from observed H01-H16 and Q001-Q024 material and from candidate "
    "outputs; Q001-Q024 may not be repaired, traced, renamed, or reused as future held-out cases."
)
RETIRED_BINDING_V2 = {
    "classification": "observed-invalid-retired-not-future-heldout-evidence",
    "corpusId": "mangasensei-reading-order-post-v2-heldout-v1",
    "corpusVersion": "1.0.0",
    "manifestGitBlobSha": "0f913fde5a302ae9c254bdcbc9956522e0451d31",
    "manifestSha256": "8bc6f0f7a173e618f4929d30b727ea3e58df6addf1f9a0e07585548f2088f62e",
    "qualificationIdentity": (
        "ropv2q-e9fd2e87e7d7a0a20c3bed83220b49a210455cdbc7af354c4d4d176b08ac2308"
    ),
    "qualificationRunId": 31982883447,
    "reuseForbidden": True,
}
WORKFLOW_CHECKS_V2 = [
    "retired post-v2 held-out v1 corpus identity/content reuse rejected",
    "sealed corpus PNG streams pass strict RGB8 CRC and complete IDAT decode validation",
]

ALLOWED_SOURCE_BINDING_OVERRIDES = {
    ".github/workflows/reading-order-post-v2-qualification.yml": "github-qualification-workflow",
    "assets/reading-order-post-v2/heldout-v1/manifest.json": (
        "retired-post-v2-heldout-v1-content-hash-ledger"
    ),
    "scripts/reading_order_post_v2_qualification/__init__.py": "qualification-package-identity",
    "scripts/reading_order_post_v2_qualification/evidence.py": "evidence-packager",
    "scripts/reading_order_post_v2_qualification/png_integrity.py": "qualification-png-integrity",
    "scripts/reading_order_post_v2_qualification/preflight.py": "preflight-validator",
    "scripts/reading_order_post_v2_qualification/retired_guard.py": (
        "retired-post-v2-heldout-v1-reuse-guard"
    ),
    "scripts/reading_order_post_v2_qualification/spec.py": "spec-validator",
    LEGACY_SPEC_REPO_PATH: "frozen-v1-scientific-methodology-base",
}


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


def _resolve_v2_overlay(path: Path) -> dict[str, Any]:
    overlay = _load(path)
    required_overlay_keys = {
        "schemaVersion",
        "experimentId",
        "repository",
        "status",
        "baseSpec",
        "infrastructureOverlay",
    }
    if set(overlay) != required_overlay_keys:
        raise SpecError("v2 experiment overlay shape changed")
    if overlay["schemaVersion"] != SPEC_SCHEMA_VERSION:
        if overlay["schemaVersion"] == LEGACY_SPEC_SCHEMA_VERSION:
            raise SpecError("historical v1 experiment spec is retired and non-executable")
        raise SpecError("wrong experiment spec schema version")
    if overlay["experimentId"] != EXPERIMENT_ID:
        raise SpecError("wrong experiment identity")
    if overlay["repository"] != "Gyliardson/mangasensei":
        raise SpecError("wrong canonical repository")
    if overlay["status"] != "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION":
        raise SpecError("experiment spec status changed")

    expected_base = {
        "path": LEGACY_SPEC_REPO_PATH,
        "schemaVersion": LEGACY_SPEC_SCHEMA_VERSION,
        "experimentId": LEGACY_EXPERIMENT_ID,
        "sha256": LEGACY_SPEC_SHA256,
        "gitBlobSha": LEGACY_SPEC_GIT_BLOB,
    }
    if overlay["baseSpec"] != expected_base:
        raise SpecError("v2 base scientific spec identity changed")
    if sha256_path(LEGACY_SPEC_PATH) != LEGACY_SPEC_SHA256:
        raise SpecError("historical v1 experiment spec SHA-256 changed")
    if _git("rev-parse", f"HEAD:{LEGACY_SPEC_REPO_PATH}") != LEGACY_SPEC_GIT_BLOB:
        raise SpecError("historical v1 experiment spec Git blob changed")

    base = _load(LEGACY_SPEC_PATH)
    if base.get("schemaVersion") != LEGACY_SPEC_SCHEMA_VERSION:
        raise SpecError("historical v1 spec schema changed")
    if base.get("experimentId") != LEGACY_EXPERIMENT_ID:
        raise SpecError("historical v1 experiment identity changed")
    if base.get("status") != "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION":
        raise SpecError("historical v1 spec status changed")

    raw_infra = overlay["infrastructureOverlay"]
    if not isinstance(raw_infra, dict) or set(raw_infra) != {
        "freezeBoundaryAdditions",
        "newHeldoutAuthoringBoundary",
        "retiredPostV2HeldoutV1Binding",
        "sourceBindingOverrides",
        "workflowPreEnvironmentChecksAppend",
    }:
        raise SpecError("v2 infrastructure overlay shape changed")
    if raw_infra["freezeBoundaryAdditions"] != {"noRetiredPostV2HeldoutV1Reuse": True}:
        raise SpecError("v2 freeze-boundary additions changed")
    if raw_infra["newHeldoutAuthoringBoundary"] != AUTHORING_BOUNDARY_V2:
        raise SpecError("v2 future-heldout authoring boundary changed")
    if raw_infra["retiredPostV2HeldoutV1Binding"] != RETIRED_BINDING_V2:
        raise SpecError("retired post-v2 held-out binding changed")
    if raw_infra["workflowPreEnvironmentChecksAppend"] != WORKFLOW_CHECKS_V2:
        raise SpecError("v2 workflow pre-environment checks changed")

    raw_overrides = raw_infra["sourceBindingOverrides"]
    if not isinstance(raw_overrides, list):
        raise SpecError("v2 source binding overrides missing")
    overrides: dict[str, dict[str, str]] = {}
    for index, record in enumerate(raw_overrides):
        if not isinstance(record, dict) or set(record) != {"path", "role", "gitBlobSha"}:
            raise SpecError(f"sourceBindingOverrides[{index}] malformed")
        repo_path, role, blob = record["path"], record["role"], record["gitBlobSha"]
        if not all(isinstance(item, str) and item for item in (repo_path, role, blob)):
            raise SpecError(f"sourceBindingOverrides[{index}] invalid")
        if repo_path in overrides:
            raise SpecError("duplicate v2 source binding override")
        overrides[repo_path] = {"path": repo_path, "role": role, "gitBlobSha": blob}
    if set(overrides) != set(ALLOWED_SOURCE_BINDING_OVERRIDES):
        raise SpecError("v2 source binding override path set changed")
    for repo_path, expected_role in ALLOWED_SOURCE_BINDING_OVERRIDES.items():
        if overrides[repo_path]["role"] != expected_role:
            raise SpecError(f"v2 source binding role changed: {repo_path}")
        if _git("rev-parse", f"HEAD:{repo_path}") != overrides[repo_path]["gitBlobSha"]:
            raise SpecError(f"v2 source binding does not match HEAD: {repo_path}")

    resolved = copy.deepcopy(base)
    resolved["schemaVersion"] = SPEC_SCHEMA_VERSION
    resolved["experimentId"] = EXPERIMENT_ID
    freeze = resolved.get("freezeBoundaries")
    if not isinstance(freeze, dict):
        raise SpecError("historical v1 freeze boundaries missing")
    freeze.update(raw_infra["freezeBoundaryAdditions"])
    design_coverage = resolved.get("newHeldoutDesignCoverage")
    if not isinstance(design_coverage, dict):
        raise SpecError("historical v1 held-out design coverage missing")
    design_coverage["authoringBoundary"] = AUTHORING_BOUNDARY_V2
    resolved["retiredPostV2HeldoutV1Binding"] = copy.deepcopy(RETIRED_BINDING_V2)

    raw_bindings = resolved.get("sourceBindings")
    if not isinstance(raw_bindings, list):
        raise SpecError("historical v1 source bindings missing")
    bindings_by_path: dict[str, dict[str, str]] = {}
    for record in raw_bindings:
        if not isinstance(record, dict) or set(record) != {"path", "role", "gitBlobSha"}:
            raise SpecError("historical v1 source binding malformed")
        bindings_by_path[str(record["path"])] = dict(record)
    bindings_by_path.update(overrides)
    resolved["sourceBindings"] = [bindings_by_path[name] for name in sorted(bindings_by_path)]

    workflow_contract = resolved.get("workflowContract")
    if not isinstance(workflow_contract, dict):
        raise SpecError("historical v1 workflow contract missing")
    workflow_binding = overrides[".github/workflows/reading-order-post-v2-qualification.yml"]
    workflow_contract["gitBlobSha"] = workflow_binding["gitBlobSha"]
    checks = workflow_contract.get("preEnvironmentChecks")
    if not isinstance(checks, list) or not all(isinstance(item, str) for item in checks):
        raise SpecError("historical v1 workflow pre-environment checks missing")
    workflow_contract["preEnvironmentChecks"] = [*checks, *WORKFLOW_CHECKS_V2]
    return resolved


def _validate_resolved_spec(spec: dict[str, Any]) -> None:
    if spec.get("schemaVersion") != SPEC_SCHEMA_VERSION:
        raise SpecError("wrong resolved experiment spec schema version")
    if spec.get("experimentId") != EXPERIMENT_ID:
        raise SpecError("wrong resolved experiment identity")
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

    current_baseline = spec.get("baselineProductionBinding")
    historical_baseline = spec.get("historicalV2ProductionBaselineBinding")
    if not isinstance(current_baseline, dict) or not isinstance(historical_baseline, dict):
        raise SpecError("production baseline bindings missing")
    expected_binding_keys = {"commitSha", "treeSha", "sourcePath", "sourceBlobSha", "role"}
    if set(current_baseline) != expected_binding_keys:
        raise SpecError("current production baseline binding shape changed")
    historical_keys = expected_binding_keys | {
        "contentEquivalentToCurrentPostV2Baseline",
        "relationship",
    }
    if set(historical_baseline) != historical_keys:
        raise SpecError("historical v2 production baseline binding shape changed")
    if current_baseline != {
        "commitSha": "f45facb2284d740df2f294800f705414e0ba465e",
        "treeSha": "68418482b8ccf5d7a3cb1c9ef3834505bd20cd4c",
        "sourcePath": "backend/src/mangasensei/ocr/reading_order.py",
        "sourceBlobSha": "12358a59deee7bd0ec0845963da1b98f031592f1",
        "role": (
            "current-post-v2-production-reading-order-dependency-not-modified-or-"
            "activated-by-this-experiment"
        ),
    }:
        raise SpecError("current post-v2 production baseline identity changed")
    if historical_baseline != {
        "commitSha": "292f0a8c8142d919ac4184159d102789c43b4116",
        "treeSha": "6605f6de429b318139fb91a4535ebbd2193508ce",
        "sourcePath": "backend/src/mangasensei/ocr/reading_order.py",
        "sourceBlobSha": "122f575c1c3567787aec29da0b1996fe0bf3e110",
        "role": "historical-reading-order-v2-production-baseline-identity",
        "contentEquivalentToCurrentPostV2Baseline": False,
        "relationship": (
            "Historical Reading Order v2 production baseline identity only; the post-v2 "
            "candidate is evaluated against the current production dependency recorded in "
            "baselineProductionBinding. The source blobs are intentionally distinct."
        ),
    }:
        raise SpecError("historical v2 production baseline identity changed")
    for label, binding in (("current", current_baseline), ("historical", historical_baseline)):
        binding_commit = str(binding["commitSha"])
        binding_tree = str(binding["treeSha"])
        binding_path = str(binding["sourcePath"])
        binding_blob = str(binding["sourceBlobSha"])
        if _git("rev-parse", f"{binding_commit}^{{tree}}") != binding_tree:
            raise SpecError(f"{label} production baseline commit/tree binding mismatch")
        if _git("rev-parse", f"{binding_commit}:{binding_path}") != binding_blob:
            raise SpecError(f"{label} production baseline source blob binding mismatch")
    if current_baseline["sourceBlobSha"] == historical_baseline["sourceBlobSha"]:
        raise SpecError("historical/current production baseline distinction disappeared")

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


def validate_spec(path: Path, *, expected_sha256: str | None = None) -> dict[str, Any]:
    if expected_sha256 is not None and sha256_path(path) != expected_sha256:
        raise SpecError("experiment spec SHA-256 mismatch")
    raw = _load(path)
    if raw.get("schemaVersion") == LEGACY_SPEC_SCHEMA_VERSION:
        raise SpecError("historical v1 experiment spec is retired and non-executable")
    resolved = _resolve_v2_overlay(path)
    _validate_resolved_spec(resolved)
    return resolved
