from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from scripts.reading_order_post_v2_qualification import preflight_v3, spec_v3

SPEC_SHA = "1" * 64
METHODOLOGY_SHA = "2" * 64
MANIFEST_SHA = "3" * 64
DESIGN_SHA = "4" * 64
EXECUTION_SHA = "5" * 40
TREE_SHA = "6" * 40
CANDIDATE_PATH = (
    "backend/src/mangasensei/ocr/diagnostics/reading_order_post_v2_calibration.py"
)


def _identity() -> str:
    return spec_v3.canonical_qualification_identity_v3(
        experiment_id=spec_v3.V3_EXPERIMENT_ID,
        spec_sha256=SPEC_SHA,
        methodology_sha256=METHODOLOGY_SHA,
        manifest_sha256=MANIFEST_SHA,
        design_sha256=DESIGN_SHA,
        execution_sha=EXECUTION_SHA,
        execution_tree_sha=TREE_SHA,
    )


def _run(
    tmp_path: Path,
    *,
    git_status: str = "",
    manifest_design_sha: str = DESIGN_SHA,
    qualification_identity: str | None = None,
    resolved_experiment_id: str = spec_v3.V3_EXPERIMENT_ID,
    resolved_methodology_sha: str = METHODOLOGY_SHA,
    hash_overrides: dict[str, str] | None = None,
):
    corpus = tmp_path / "sealed"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(
        json.dumps({"design": {"file": "corpus-design.json", "sha256": manifest_design_sha}}),
        encoding="utf-8",
    )
    (corpus / "corpus-design.json").write_text("{}", encoding="utf-8")
    spec_path = tmp_path / "experiment-spec-v3.json"
    spec_path.write_text("{}", encoding="utf-8")
    git_values = {
        ("rev-parse", "HEAD"): EXECUTION_SHA,
        ("rev-parse", "HEAD^{tree}"): TREE_SHA,
        ("status", "--porcelain"): git_status,
    }
    hashes = {
        spec_path: SPEC_SHA,
        spec_v3.METHODOLOGY_PATH: METHODOLOGY_SHA,
        corpus / "manifest.json": MANIFEST_SHA,
        corpus / "corpus-design.json": DESIGN_SHA,
    }
    for name, value in (hash_overrides or {}).items():
        hashes[{
            "methodology": spec_v3.METHODOLOGY_PATH,
            "manifest": corpus / "manifest.json",
            "design": corpus / "corpus-design.json",
        }[name]] = value
    resolved = {
        "experimentId": resolved_experiment_id,
        "methodology": {"sha256": resolved_methodology_sha},
        "candidateBinding": {"path": CANDIDATE_PATH},
    }
    with (
        patch.object(preflight_v3, "_git", side_effect=lambda *args: git_values[args]),
        patch.object(preflight_v3, "sha256_path", side_effect=lambda path: hashes[path]),
        patch.object(preflight_v3, "validate_spec_v3", return_value=resolved) as validate_spec,
        patch.object(preflight_v3, "_validate_runtime_candidate") as runtime_candidate,
    ):
        context = preflight_v3.validate_preflight_v3(
            corpus_root=corpus,
            spec_path=spec_path,
            experiment_id=spec_v3.V3_EXPERIMENT_ID,
            expected_spec_sha256=SPEC_SHA,
            expected_methodology_sha256=METHODOLOGY_SHA,
            expected_manifest_sha256=MANIFEST_SHA,
            expected_design_sha256=DESIGN_SHA,
            qualification_identity=qualification_identity or _identity(),
            execution_sha=EXECUTION_SHA,
            expected_tree_sha=TREE_SHA,
        )
    return context, validate_spec, runtime_candidate


def test_preflight_v3_returns_validated_context_and_runs_strict_clean_room_gates(
    tmp_path: Path,
) -> None:
    context, validate_spec, runtime_candidate = _run(tmp_path)
    validate_spec.assert_called_once_with(validate_spec.call_args.args[0], expected_sha256=SPEC_SHA)
    runtime_candidate.assert_called_once_with(context.spec)
    assert context.experiment_id == spec_v3.V3_EXPERIMENT_ID
    assert context.qualification_identity == _identity()
    assert context.coverage is None


@pytest.mark.parametrize(
    ("command", "actual", "message"),
    [
        (("rev-parse", "HEAD"), "7" * 40, "HEAD"),
        (("rev-parse", "HEAD^{tree}"), "8" * 40, "tree"),
        (("status", "--porcelain"), " M unrelated", "clean"),
    ],
)
def test_preflight_v3_rejects_wrong_head_tree_or_dirty_checkout(
    tmp_path: Path, command: tuple[str, str], actual: str, message: str
) -> None:
    corpus = tmp_path / "corpus"
    values = {
        ("rev-parse", "HEAD"): EXECUTION_SHA,
        ("rev-parse", "HEAD^{tree}"): TREE_SHA,
        ("status", "--porcelain"): "",
    }
    values[command] = actual
    with (
        patch.object(preflight_v3, "_git", side_effect=lambda *args: values[args]),
        pytest.raises((ValueError, RuntimeError), match=message),
    ):
        preflight_v3.validate_preflight_v3(
            corpus_root=corpus,
            spec_path=tmp_path / "spec.json",
            experiment_id=spec_v3.V3_EXPERIMENT_ID,
            expected_spec_sha256=SPEC_SHA,
            expected_methodology_sha256=METHODOLOGY_SHA,
            expected_manifest_sha256=MANIFEST_SHA,
            expected_design_sha256=DESIGN_SHA,
            qualification_identity=_identity(),
            execution_sha=EXECUTION_SHA,
            expected_tree_sha=TREE_SHA,
        )


def test_preflight_v3_rejects_manifest_design_binding_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bind.*design"):
        _run(tmp_path, manifest_design_sha="9" * 64)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"resolved_experiment_id": "wrong"}, "experiment identity"),
        ({"resolved_methodology_sha": "0" * 64}, "methodology"),
        ({"hash_overrides": {"methodology": "0" * 64}}, "methodology"),
        ({"hash_overrides": {"manifest": "0" * 64}}, "manifest"),
        ({"hash_overrides": {"design": "0" * 64}}, "design"),
    ],
)
def test_preflight_v3_rejects_dispatch_and_frozen_hash_drift(
    tmp_path: Path, kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _run(tmp_path, **kwargs)


def test_preflight_v3_rejects_methodology_hash_mismatch(tmp_path: Path) -> None:
    original = preflight_v3.sha256_path
    with (
        patch.object(
            preflight_v3,
            "sha256_path",
            side_effect=lambda path: (
                "0" * 64 if path == spec_v3.METHODOLOGY_PATH else original(path)
            ),
        ),
        pytest.raises(ValueError, match="methodology"),
    ):
        # _run owns the complete mock set, so exercise the gate directly through its helper.
        preflight_v3._validate_methodology_hash("f" * 64)


def test_preflight_v3_rejects_non_object_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        preflight_v3._load_manifest(manifest)


def test_preflight_v3_rejects_wrong_canonical_identity(tmp_path: Path) -> None:
    with pytest.raises(spec_v3.SpecV3Error, match="identity"):
        _run(tmp_path, qualification_identity="ropv3q-" + "0" * 64)


def _candidate_spec() -> dict[str, object]:
    return {"candidateBinding": {"path": CANDIDATE_PATH}}


def test_runtime_candidate_accepts_exact_import_origin_and_head_bytes() -> None:
    origin = Path(preflight_v3.runtime_candidate_module.__file__).resolve(strict=True)
    with patch.object(preflight_v3, "_git_bytes", return_value=origin.read_bytes()) as git_bytes:
        preflight_v3._validate_runtime_candidate(_candidate_spec())
    git_bytes.assert_called_once_with("show", f"HEAD:{CANDIDATE_PATH}")


def test_runtime_candidate_rejects_wrong_module_origin(tmp_path: Path) -> None:
    substituted_path = tmp_path / "reading_order_post_v2_calibration.py"
    substituted_path.write_bytes(b"substituted")
    substituted = SimpleNamespace(__file__=str(substituted_path))
    with (
        patch.object(preflight_v3, "runtime_candidate_module", substituted),
        patch.object(preflight_v3.exercise_v3, "candidate_module", substituted),
        pytest.raises(RuntimeError, match="origin"),
    ):
        preflight_v3._validate_runtime_candidate(_candidate_spec())


def test_runtime_candidate_rejects_different_exercise_module_object() -> None:
    with (
        patch.object(preflight_v3.exercise_v3, "candidate_module", object()),
        pytest.raises(RuntimeError, match="same module object"),
    ):
        preflight_v3._validate_runtime_candidate(_candidate_spec())


def test_runtime_candidate_rejects_bytes_different_from_head() -> None:
    with (
        patch.object(preflight_v3, "_git_bytes", return_value=b"not-the-candidate"),
        pytest.raises(RuntimeError, match="bytes"),
    ):
        preflight_v3._validate_runtime_candidate(_candidate_spec())


def test_runtime_candidate_rejects_stale_loader_code() -> None:
    origin = Path(preflight_v3.runtime_candidate_module.__file__).resolve(strict=True)
    stale = compile("STALE = True\n", str(origin), "exec")
    with (
        patch.object(preflight_v3, "_git_bytes", return_value=origin.read_bytes()),
        patch.object(preflight_v3, "_loaded_module_code", return_value=stale),
        pytest.raises(RuntimeError, match="loaded code"),
    ):
        preflight_v3._validate_runtime_candidate(_candidate_spec())


def test_runtime_candidate_rejects_missing_candidate_binding() -> None:
    with pytest.raises(RuntimeError, match="path binding"):
        preflight_v3._validate_runtime_candidate({})


@pytest.mark.parametrize(
    "module",
    [SimpleNamespace(), SimpleNamespace(__file__="missing-candidate.py")],
)
def test_runtime_candidate_rejects_missing_or_unresolvable_origin(module: object) -> None:
    with (
        patch.object(preflight_v3, "runtime_candidate_module", module),
        pytest.raises(RuntimeError, match="origin"),
    ):
        preflight_v3._validate_runtime_candidate(_candidate_spec())
