from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from scripts.reading_order_v3_authoring.contracts import AUTHORING_SLICES

from .canonical import sha256_path
from .exercise_v3 import EXERCISE_MINIMA_V3
from .spec import REPO_ROOT, validate_spec

V3_SPEC_SCHEMA_VERSION = "reading-order-post-v2-experiment-spec-v3"
V3_EXPERIMENT_ID = "reading-order-post-v2-c1-c2-c3-b1-v3"
V3_SPEC_REPO_PATH = (
    "scripts/reading_order_post_v2_qualification/spec/experiment-spec-v3.json"
)
BASE_V2_SPEC_REPO_PATH = (
    "scripts/reading_order_post_v2_qualification/spec/experiment-spec-v2.json"
)
METHODOLOGY_REPO_PATH = (
    "scripts/reading_order_post_v2_qualification/spec/methodology-v3.json"
)
V3_SPEC_PATH = REPO_ROOT / V3_SPEC_REPO_PATH
BASE_V2_SPEC_PATH = REPO_ROOT / BASE_V2_SPEC_REPO_PATH
METHODOLOGY_PATH = REPO_ROOT / METHODOLOGY_REPO_PATH

BASE_V2_BINDING = {
    "path": BASE_V2_SPEC_REPO_PATH,
    "sha256": "83ce58ab24c74433fea4b0e8367ad8508368d1fb9114c76b37192c736502ec33",
    "gitBlobSha": "b8b08da5fa30ddf370c663e540c5cdc30d5854c3",
}
METHODOLOGY_BINDING = {
    "path": METHODOLOGY_REPO_PATH,
    "sha256": "7d415a2221fedc173eb28ae0d6939109b7a4b2c5ce3fab252707dde619864ea8",
    "gitBlobSha": "a813784e641a68e047c7c70164aeb1129f75c221",
}
WORKFLOW_INTEGRATION = {
    "present": False,
    "dispatchable": False,
    "status": "PENDING_PR_B",
    "inheritedV2WorkflowExecutableBinding": False,
}
V3_OVERLAY_KEYS = frozenset(
    {
        "schemaVersion",
        "experimentId",
        "repository",
        "status",
        "baseV2Spec",
        "methodology",
        "workflowIntegration",
        "authoringSlices",
        "exerciseMinima",
        "candidateBinding",
        "reviewedV3SourceClosure",
    }
)
REVIEWED_V3_SOURCE_ROLES = {
    "backend/src/mangasensei/__init__.py": "mangasensei-package-initializer",
    "backend/src/mangasensei/ocr/__init__.py": "ocr-package-initializer",
    "backend/src/mangasensei/ocr/diagnostics/__init__.py": (
        "diagnostics-package-initializer"
    ),
    "backend/src/mangasensei/ocr/vendor/__init__.py": "ocr-vendor-package-initializer",
    "backend/src/mangasensei/ocr/vendor/manga_image_translator/__init__.py": (
        "translator-vendor-package-initializer"
    ),
    "backend/src/mangasensei/ocr/vendor/manga_image_translator/"
    "manga_translator/__init__.py": "translator-package-initializer",
    "backend/src/mangasensei/ocr/vendor/manga_image_translator/"
    "manga_translator/utils/__init__.py": "translator-utils-package-initializer",
    "backend/src/mangasensei/ocr/reading_order.py": "panel-segmentation-dependency",
    "backend/src/mangasensei/ocr/diagnostics/reading_order_v2.py": (
        "frozen-local-order-dependency"
    ),
    "backend/src/mangasensei/ocr/diagnostics/reading_order_v2_contracts.py": (
        "frozen-region-contract-dependency"
    ),
    "backend/src/mangasensei/ocr/vendor/manga_image_translator/"
    "manga_translator/utils/textblock.py": "production-textblock-fixture-dependency",
    "backend/src/mangasensei/ocr/vendor/manga_image_translator/"
    "manga_translator/utils/generic2.py": (
        "production-textblock-character-and-color-helpers"
    ),
    "scripts/reading_order_post_v2_qualification/__init__.py": (
        "qualification-schema-identities"
    ),
    "scripts/reading_order_post_v2_qualification/canonical.py": "canonical-serialization",
    "scripts/reading_order_post_v2_qualification/contracts.py": (
        "inherited-scientific-contracts"
    ),
    "scripts/reading_order_post_v2_qualification/exercise.py": (
        "inherited-non-c3-exercise-evaluator"
    ),
    "scripts/reading_order_post_v2_qualification/fixtures.py": (
        "production-shaped-region-fixtures"
    ),
    "scripts/reading_order_post_v2_qualification/spec.py": "validated-v2-base-resolver",
    "scripts/reading_order_post_v2_qualification/spec_v3.py": (
        "v3-overlay-validator-and-identity"
    ),
    "scripts/reading_order_post_v2_qualification/preflight_v3.py": (
        "v3-fail-closed-preflight"
    ),
    "scripts/reading_order_post_v2_qualification/historical_guard.py": (
        "historical-v2-content-reuse-guard"
    ),
    "scripts/reading_order_post_v2_qualification/retired_guard.py": (
        "retired-post-v2-v1-content-reuse-guard"
    ),
    "scripts/reading_order_post_v2_qualification/run_arm.py": (
        "frozen-arm-config-and-serialization-reference"
    ),
    "scripts/reading_order_post_v2_qualification/run_arm_v3.py": (
        "v3-clean-room-one-page-arm-runner"
    ),
    "scripts/reading_order_post_v2_qualification/run_v3.py": (
        "v3-top-level-qualification-runner"
    ),
    "scripts/reading_order_post_v2_qualification/scoring.py": "frozen-scoring-implementation",
    "scripts/reading_order_post_v2_qualification/exercise_v3.py": (
        "v3-runtime-exercise-evaluator"
    ),
    "scripts/reading_order_post_v2_qualification/v3_clean_room_compat.py": (
        "v3-clean-room-runtime-adapter"
    ),
    "scripts/reading_order_post_v2_qualification/verdict.py": (
        "historical-verdict-dependency"
    ),
    "scripts/reading_order_post_v2_qualification/verdict_v3.py": "v3-verdict-adapter",
    "scripts/reading_order_v3_authoring/__init__.py": "v3-clean-room-public-contract",
    "scripts/reading_order_v3_authoring/canonical.py": "v3-clean-room-canonicalization",
    "scripts/reading_order_v3_authoring/contracts.py": "v3-clean-room-corpus-contracts",
    "scripts/reading_order_v3_authoring/validate.py": (
        "v3-strict-clean-room-corpus-validator"
    ),
}
CANDIDATE_BINDING = {
    "commitSha": "f45facb2284d740df2f294800f705414e0ba465e",
    "treeSha": "68418482b8ccf5d7a3cb1c9ef3834505bd20cd4c",
    "path": (
        "backend/src/mangasensei/ocr/diagnostics/"
        "reading_order_post_v2_calibration.py"
    ),
    "gitBlobSha": "ed1be14f4ad47c317ad755b94f1b3e23e84064da",
}

_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class SpecV3Error(ValueError):
    pass


def _git(*args: str) -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git is required for frozen v3 source validation")
    result = subprocess.run(  # noqa: S603
        [git, *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SpecV3Error("v3 experiment spec must be a JSON object")
    return value


def _validate_frozen_file(binding: dict[str, str], path: Path, label: str) -> None:
    if sha256_path(path) != binding["sha256"]:
        raise SpecV3Error(f"{label} SHA-256 changed")
    if _git("rev-parse", f'HEAD:{binding["path"]}') != binding["gitBlobSha"]:
        raise SpecV3Error(f"{label} Git blob changed")


def _validate_methodology() -> None:
    methodology = _load_object(METHODOLOGY_PATH)
    if methodology.get("schemaVersion") != "reading-order-post-v2-methodology-v3":
        raise SpecV3Error("methodology schema changed")
    if methodology.get("experimentId") != V3_EXPERIMENT_ID:
        raise SpecV3Error("methodology experiment identity changed")
    if methodology.get("status") != "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION":
        raise SpecV3Error("methodology status changed")
    if methodology.get("currentWorkflowDispatchable") is not False:
        raise SpecV3Error("methodology workflow state changed")
    if methodology.get("baseV2Spec") != {
        **BASE_V2_BINDING,
        "schemaVersion": "reading-order-post-v2-experiment-spec-v2",
        "experimentId": "reading-order-post-v2-c1-c2-c3-b1-v2",
    }:
        raise SpecV3Error("methodology v2 base binding changed")
    candidate = methodology.get("candidateBinding")
    if not isinstance(candidate, dict):
        raise SpecV3Error("methodology candidate binding missing")
    if {key: candidate.get(key) for key in CANDIDATE_BINDING} != CANDIDATE_BINDING:
        raise SpecV3Error("methodology candidate binding changed")


def _validate_candidate(candidate: object) -> None:
    if candidate != CANDIDATE_BINDING:
        raise SpecV3Error("v3 candidate binding changed")
    commit = CANDIDATE_BINDING["commitSha"]
    tree = CANDIDATE_BINDING["treeSha"]
    path = CANDIDATE_BINDING["path"]
    blob = CANDIDATE_BINDING["gitBlobSha"]
    if _git("rev-parse", f"{commit}^{{tree}}") != tree:
        raise SpecV3Error("v3 candidate commit/tree binding mismatch")
    if _git("rev-parse", f"{commit}:{path}") != blob:
        raise SpecV3Error("v3 candidate commit/source binding mismatch")
    if _git("rev-parse", f"HEAD:{path}") != blob:
        raise SpecV3Error("v3 candidate source changed in execution tree")


def _validate_source_closure(value: object) -> None:
    if not isinstance(value, list) or len(value) != len(REVIEWED_V3_SOURCE_ROLES):
        raise SpecV3Error("reviewed v3 source closure path set changed")
    actual: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, binding in enumerate(value):
        if not isinstance(binding, dict) or set(binding) != {"path", "role", "gitBlobSha"}:
            raise SpecV3Error(f"reviewed v3 source closure binding {index} malformed")
        path, role, blob = binding["path"], binding["role"], binding["gitBlobSha"]
        if not all(isinstance(item, str) and item for item in (path, role, blob)):
            raise SpecV3Error(f"reviewed v3 source closure binding {index} invalid")
        if path in seen or path == V3_SPEC_REPO_PATH:
            raise SpecV3Error("reviewed v3 source closure contains duplicate or self binding")
        seen.add(path)
        actual.append((path, role))
        if _HEX40_RE.fullmatch(blob) is None or _git("rev-parse", f"HEAD:{path}") != blob:
            raise SpecV3Error(f"reviewed v3 source closure binding changed: {path}")
    if actual != list(REVIEWED_V3_SOURCE_ROLES.items()):
        raise SpecV3Error("reviewed v3 source closure path or role changed")


def validate_spec_v3(
    path: Path = V3_SPEC_PATH, *, expected_sha256: str | None = None
) -> dict[str, Any]:
    if expected_sha256 is not None and sha256_path(path) != expected_sha256:
        raise SpecV3Error("v3 experiment spec SHA-256 mismatch")
    overlay = _load_object(path)
    if set(overlay) != V3_OVERLAY_KEYS:
        raise SpecV3Error("v3 experiment overlay shape changed")
    if overlay["schemaVersion"] != V3_SPEC_SCHEMA_VERSION:
        raise SpecV3Error("v3 experiment schema changed")
    if overlay["experimentId"] != V3_EXPERIMENT_ID:
        raise SpecV3Error("v3 experiment identity changed")
    if overlay["repository"] != "Gyliardson/mangasensei":
        raise SpecV3Error("v3 repository identity changed")
    if overlay["status"] != "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION":
        raise SpecV3Error("v3 experiment status changed")
    if overlay["baseV2Spec"] != BASE_V2_BINDING:
        raise SpecV3Error("v3 base v2 binding changed")
    if overlay["methodology"] != METHODOLOGY_BINDING:
        raise SpecV3Error("v3 methodology binding changed")
    if overlay["workflowIntegration"] != WORKFLOW_INTEGRATION:
        raise SpecV3Error("v3 workflow integration is not pending and non-executable")
    if overlay["authoringSlices"] != list(AUTHORING_SLICES):
        raise SpecV3Error("frozen v3 authoring slices changed")
    if overlay["exerciseMinima"] != EXERCISE_MINIMA_V3:
        raise SpecV3Error("frozen v3 exercise minima changed")

    _validate_frozen_file(BASE_V2_BINDING, BASE_V2_SPEC_PATH, "base v2 spec")
    resolved_v2 = validate_spec(BASE_V2_SPEC_PATH)
    _validate_frozen_file(METHODOLOGY_BINDING, METHODOLOGY_PATH, "methodology")
    _validate_methodology()
    _validate_candidate(overlay["candidateBinding"])
    _validate_source_closure(overlay["reviewedV3SourceClosure"])
    return {**copy.deepcopy(overlay), "resolvedBaseV2": resolved_v2}


def canonical_qualification_identity_v3(
    *,
    experiment_id: str,
    spec_sha256: str,
    methodology_sha256: str,
    manifest_sha256: str,
    design_sha256: str,
    execution_sha: str,
    execution_tree_sha: str,
) -> str:
    values = (
        spec_sha256,
        methodology_sha256,
        manifest_sha256,
        design_sha256,
    )
    if not isinstance(experiment_id, str) or not experiment_id:
        raise SpecV3Error("qualification experiment identity is invalid")
    if any(not isinstance(value, str) or _HEX64_RE.fullmatch(value) is None for value in values):
        raise SpecV3Error("qualification SHA-256 input is invalid")
    if any(
        not isinstance(value, str) or _HEX40_RE.fullmatch(value) is None
        for value in (execution_sha, execution_tree_sha)
    ):
        raise SpecV3Error("qualification Git SHA input is invalid")
    payload = {
        "experimentId": experiment_id,
        "specSha256": spec_sha256,
        "methodologySha256": methodology_sha256,
        "manifestSha256": manifest_sha256,
        "designSha256": design_sha256,
        "executionSha": execution_sha,
        "executionTreeSha": execution_tree_sha,
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "ropv3q-" + hashlib.sha256(canonical).hexdigest()


def validate_qualification_identity_v3(identity: str, **parts: str) -> None:
    expected = canonical_qualification_identity_v3(**parts)
    if not isinstance(identity, str) or not hmac.compare_digest(identity, expected):
        raise SpecV3Error("v3 qualification identity does not match frozen inputs")
