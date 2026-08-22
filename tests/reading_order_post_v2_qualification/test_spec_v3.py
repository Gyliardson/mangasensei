from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.reading_order_post_v2_qualification import spec_v3

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = (
    REPO_ROOT
    / "scripts"
    / "reading_order_post_v2_qualification"
    / "spec"
    / "experiment-spec-v3.json"
)


def _raw_spec() -> dict[str, object]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def _validate(raw: dict[str, object] | None = None, tmp_path: Path | None = None):
    path = SPEC_PATH
    if raw is not None:
        assert tmp_path is not None
        path = tmp_path / "experiment-spec-v3.json"
        path.write_text(json.dumps(raw), encoding="utf-8")

    with (
        patch.object(spec_v3, "validate_spec", return_value={"v2": "validated"}) as base,
        patch.object(
            spec_v3,
            "_git",
            side_effect=lambda *args: _git_answer(raw or _raw_spec(), *args),
        ),
    ):
        result = spec_v3.validate_spec_v3(path)
    base.assert_called_once_with(spec_v3.BASE_V2_SPEC_PATH)
    return result


def _git_answer(raw: dict[str, object], *args: str) -> str:
    assert args[0] == "rev-parse"
    revision = args[1]
    for key in ("baseV2Spec", "methodology"):
        binding = raw[key]
        assert isinstance(binding, dict)
        if revision == f'HEAD:{binding["path"]}':
            return str(binding["gitBlobSha"])
    candidate = raw["candidateBinding"]
    assert isinstance(candidate, dict)
    if revision == f'{candidate["commitSha"]}^{{tree}}':
        return str(candidate["treeSha"])
    if revision in {
        f'{candidate["commitSha"]}:{candidate["path"]}',
        f'HEAD:{candidate["path"]}',
    }:
        return str(candidate["gitBlobSha"])
    bindings = raw["reviewedV3SourceClosure"]
    assert isinstance(bindings, list)
    by_revision = {f'HEAD:{item["path"]}': item["gitBlobSha"] for item in bindings}
    return str(by_revision[revision])


def test_v3_overlay_has_exact_shape_and_distinct_identity() -> None:
    raw = _raw_spec()
    assert set(raw) == spec_v3.V3_OVERLAY_KEYS
    assert raw["schemaVersion"] == spec_v3.V3_SPEC_SCHEMA_VERSION
    assert raw["experimentId"] == spec_v3.V3_EXPERIMENT_ID
    assert raw["status"] == "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION"


def test_v3_overlay_binds_base_and_methodology_by_path_hash_and_blob() -> None:
    raw = _raw_spec()
    assert raw["baseV2Spec"] == {
        "path": spec_v3.BASE_V2_SPEC_REPO_PATH,
        "sha256": "83ce58ab24c74433fea4b0e8367ad8508368d1fb9114c76b37192c736502ec33",
        "gitBlobSha": "b8b08da5fa30ddf370c663e540c5cdc30d5854c3",
    }
    assert raw["methodology"] == {
        "path": spec_v3.METHODOLOGY_REPO_PATH,
        "sha256": "7d415a2221fedc173eb28ae0d6939109b7a4b2c5ce3fab252707dde619864ea8",
        "gitBlobSha": "a813784e641a68e047c7c70164aeb1129f75c221",
    }


def test_v3_workflow_is_explicitly_absent_pending_pr_b() -> None:
    workflow = _raw_spec()["workflowIntegration"]
    assert workflow == {
        "present": False,
        "dispatchable": False,
        "status": "PENDING_PR_B",
        "inheritedV2WorkflowExecutableBinding": False,
    }


def test_v3_freezes_authoring_slices_and_exercise_minima() -> None:
    raw = _raw_spec()
    assert raw["authoringSlices"] == list(spec_v3.AUTHORING_SLICES)
    assert raw["exerciseMinima"] == spec_v3.EXERCISE_MINIMA_V3


def test_v3_source_closure_is_exact_and_has_no_spec_self_binding() -> None:
    bindings = _raw_spec()["reviewedV3SourceClosure"]
    assert isinstance(bindings, list)
    assert [(item["path"], item["role"]) for item in bindings] == list(
        spec_v3.REVIEWED_V3_SOURCE_ROLES.items()
    )
    paths = {item["path"] for item in bindings}
    assert spec_v3.V3_SPEC_REPO_PATH not in paths
    assert ".github/workflows/reading-order-post-v2-qualification.yml" not in paths


def test_v3_candidate_is_frozen_and_validated_against_head() -> None:
    resolved = _validate()
    assert resolved["candidateBinding"] == _raw_spec()["candidateBinding"]
    assert resolved["resolvedBaseV2"] == {"v2": "validated"}


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schemaVersion", "reading-order-post-v2-experiment-spec-v2", "schema"),
        ("experimentId", "reading-order-post-v2-c1-c2-c3-b1-v2", "identity"),
        ("status", "AUTHORIZED", "status"),
    ],
)
def test_v3_rejects_wrong_identity_fields(
    tmp_path: Path, field: str, value: str, message: str
) -> None:
    raw = copy.deepcopy(_raw_spec())
    raw[field] = value
    with pytest.raises(spec_v3.SpecV3Error, match=message):
        _validate(raw, tmp_path)


def test_v3_rejects_inherited_v2_workflow_as_executable_binding(tmp_path: Path) -> None:
    raw = copy.deepcopy(_raw_spec())
    workflow = raw["workflowIntegration"]
    assert isinstance(workflow, dict)
    workflow["dispatchable"] = True
    with pytest.raises(spec_v3.SpecV3Error, match="workflow"):
        _validate(raw, tmp_path)


def test_v3_rejects_source_closure_path_or_role_drift(tmp_path: Path) -> None:
    raw = copy.deepcopy(_raw_spec())
    bindings = raw["reviewedV3SourceClosure"]
    assert isinstance(bindings, list)
    bindings[0]["role"] = "changed"
    with pytest.raises(spec_v3.SpecV3Error, match="source closure"):
        _validate(raw, tmp_path)


def test_v3_rejects_spec_self_binding(tmp_path: Path) -> None:
    raw = copy.deepcopy(_raw_spec())
    bindings = raw["reviewedV3SourceClosure"]
    assert isinstance(bindings, list)
    bindings.append(
        {"path": spec_v3.V3_SPEC_REPO_PATH, "role": "self", "gitBlobSha": "1" * 40}
    )
    with pytest.raises(spec_v3.SpecV3Error, match="source closure"):
        _validate(raw, tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda bindings: bindings.__setitem__(0, "malformed"),
        lambda bindings: bindings[0].__setitem__("gitBlobSha", "not-a-blob"),
        lambda bindings: bindings[1].__setitem__("gitBlobSha", "1" * 40),
        lambda bindings: bindings[1].__setitem__("path", bindings[0]["path"]),
    ],
)
def test_v3_rejects_malformed_duplicate_or_unfinalized_source_closure(
    mutation,
) -> None:
    bindings = copy.deepcopy(_raw_spec()["reviewedV3SourceClosure"])
    assert isinstance(bindings, list)
    mutation(bindings)
    with (
        patch.object(spec_v3, "_git", return_value="0" * 40),
        pytest.raises(spec_v3.SpecV3Error, match="source closure"),
    ):
        spec_v3._validate_source_closure(bindings)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schemaVersion", "wrong", "schema"),
        ("experimentId", "wrong", "identity"),
        ("status", "AUTHORIZED", "status"),
        ("currentWorkflowDispatchable", True, "workflow"),
        ("baseV2Spec", {}, "base"),
        ("candidateBinding", {}, "candidate"),
    ],
)
def test_v3_rejects_methodology_contract_drift(field: str, value: object, message: str) -> None:
    methodology = json.loads(spec_v3.METHODOLOGY_PATH.read_text(encoding="utf-8"))
    methodology[field] = value
    with (
        patch.object(spec_v3, "_load_object", return_value=methodology),
        pytest.raises(spec_v3.SpecV3Error, match=message),
    ):
        spec_v3._validate_methodology()


def test_v3_rejects_frozen_file_hash_and_blob_drift() -> None:
    with (
        patch.object(spec_v3, "sha256_path", return_value="0" * 64),
        pytest.raises(spec_v3.SpecV3Error, match="SHA-256"),
    ):
        spec_v3._validate_frozen_file(
            spec_v3.METHODOLOGY_BINDING, spec_v3.METHODOLOGY_PATH, "methodology"
        )
    with (
        patch.object(spec_v3, "sha256_path", return_value=spec_v3.METHODOLOGY_BINDING["sha256"]),
        patch.object(spec_v3, "_git", return_value="0" * 40),
        pytest.raises(spec_v3.SpecV3Error, match="Git blob"),
    ):
        spec_v3._validate_frozen_file(
            spec_v3.METHODOLOGY_BINDING, spec_v3.METHODOLOGY_PATH, "methodology"
        )


def test_v3_spec_hash_is_externally_supplied() -> None:
    expected = hashlib.sha256(SPEC_PATH.read_bytes()).hexdigest()
    with pytest.raises(spec_v3.SpecV3Error, match="SHA-256"):
        spec_v3.validate_spec_v3(SPEC_PATH, expected_sha256="0" * 64)
    assert "sha256" not in _raw_spec()
    assert expected != "0" * 64


def test_v3_qualification_identity_uses_canonical_json_and_all_seven_fields() -> None:
    parts = {
        "experiment_id": spec_v3.V3_EXPERIMENT_ID,
        "spec_sha256": "1" * 64,
        "methodology_sha256": "2" * 64,
        "manifest_sha256": "3" * 64,
        "design_sha256": "4" * 64,
        "execution_sha": "5" * 40,
        "execution_tree_sha": "6" * 40,
    }
    payload = {
        "designSha256": parts["design_sha256"],
        "executionSha": parts["execution_sha"],
        "executionTreeSha": parts["execution_tree_sha"],
        "experimentId": parts["experiment_id"],
        "manifestSha256": parts["manifest_sha256"],
        "methodologySha256": parts["methodology_sha256"],
        "specSha256": parts["spec_sha256"],
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    expected = "ropv3q-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    identity = spec_v3.canonical_qualification_identity_v3(**parts)
    assert identity == expected
    spec_v3.validate_qualification_identity_v3(identity, **parts)
    with pytest.raises(spec_v3.SpecV3Error, match="identity"):
        spec_v3.validate_qualification_identity_v3(identity, **{**parts, "design_sha256": "7" * 64})


@pytest.mark.parametrize(
    "overrides",
    [
        {"experiment_id": ""},
        {"spec_sha256": "bad"},
        {"execution_sha": "bad"},
    ],
)
def test_v3_qualification_identity_rejects_malformed_inputs(overrides: dict[str, str]) -> None:
    parts = {
        "experiment_id": spec_v3.V3_EXPERIMENT_ID,
        "spec_sha256": "1" * 64,
        "methodology_sha256": "2" * 64,
        "manifest_sha256": "3" * 64,
        "design_sha256": "4" * 64,
        "execution_sha": "5" * 40,
        "execution_tree_sha": "6" * 40,
    }
    with pytest.raises(spec_v3.SpecV3Error, match="invalid"):
        spec_v3.canonical_qualification_identity_v3(**{**parts, **overrides})
