from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from scripts.reading_order_post_v2_qualification import historical_guard, preflight_v3, spec_v3
from scripts.reading_order_post_v2_qualification.retired_guard import RETIRED_CORPUS_ID
from scripts.reading_order_v3_authoring import write_canonical_json, write_manifest
from tests.reading_order_v3_authoring._fixtures import _png, _write

SPEC_SHA = "1" * 64
METHODOLOGY_SHA = "2" * 64
EXECUTION_SHA = "5" * 40
TREE_SHA = "6" * 40
CANDIDATE_PATH = (
    "backend/src/mangasensei/ocr/diagnostics/reading_order_post_v2_calibration.py"
)


def _identity(*, manifest_sha: str, design_sha: str) -> str:
    return spec_v3.canonical_qualification_identity_v3(
        experiment_id=spec_v3.V3_EXPERIMENT_ID,
        spec_sha256=SPEC_SHA,
        methodology_sha256=METHODOLOGY_SHA,
        manifest_sha256=manifest_sha,
        design_sha256=design_sha,
        execution_sha=EXECUTION_SHA,
        execution_tree_sha=TREE_SHA,
    )


def _run(
    tmp_path: Path,
    *,
    corpus: Path | None = None,
    manifest_design_sha: str | None = None,
    qualification_identity: str | None = None,
    resolved_experiment_id: str = spec_v3.V3_EXPERIMENT_ID,
    resolved_methodology_sha: str = METHODOLOGY_SHA,
    hash_overrides: dict[str, str] | None = None,
):
    corpus = corpus or tmp_path / "sealed"
    if not corpus.exists():
        _write(corpus)
    if manifest_design_sha is not None:
        manifest_path = corpus / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["design"]["sha256"] = manifest_design_sha
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    spec_path = tmp_path / "experiment-spec-v3.json"
    spec_path.write_text("{}", encoding="utf-8")
    manifest_sha = preflight_v3.sha256_path(corpus / "manifest.json")
    design_sha = preflight_v3.sha256_path(corpus / "corpus-design.json")
    expected_manifest_sha = (
        "0" * 64 if (hash_overrides or {}).get("manifest") else manifest_sha
    )
    expected_design_sha = "0" * 64 if (hash_overrides or {}).get("design") else design_sha
    git_values = {
        ("rev-parse", f"{EXECUTION_SHA}^{{commit}}"): EXECUTION_SHA,
        ("rev-parse", f"{EXECUTION_SHA}^{{tree}}"): TREE_SHA,
    }
    resolved = {
        "experimentId": resolved_experiment_id,
        "methodology": {"sha256": resolved_methodology_sha},
        "candidateBinding": {"path": CANDIDATE_PATH},
    }
    with (
        patch.object(preflight_v3, "_git", side_effect=lambda *args: git_values[args]),
        patch.object(
            preflight_v3,
            "_validate_methodology_hash",
            side_effect=(
                ValueError("frozen v3 methodology hash mismatch")
                if (hash_overrides or {}).get("methodology")
                else None
            ),
        ),
        patch.object(preflight_v3, "validate_spec_v3", return_value=resolved) as validate_spec,
        patch.object(preflight_v3, "_validate_runtime_candidate") as runtime_candidate,
    ):
        context = preflight_v3.validate_preflight_v3(
            corpus_root=corpus,
            spec_path=spec_path,
            experiment_id=spec_v3.V3_EXPERIMENT_ID,
            expected_spec_sha256=SPEC_SHA,
            expected_methodology_sha256=METHODOLOGY_SHA,
            expected_manifest_sha256=expected_manifest_sha,
            expected_design_sha256=expected_design_sha,
            qualification_identity=qualification_identity or _identity(
                manifest_sha=expected_manifest_sha, design_sha=expected_design_sha
            ),
            execution_sha=EXECUTION_SHA,
            expected_tree_sha=TREE_SHA,
        )
    return context, validate_spec, runtime_candidate, corpus


def test_preflight_v3_returns_validated_context_and_runs_strict_clean_room_gates(
    tmp_path: Path,
) -> None:
    context, validate_spec, runtime_candidate, _corpus = _run(tmp_path)
    validate_spec.assert_called_once_with(
        validate_spec.call_args.args[0],
        expected_sha256=SPEC_SHA,
        execution_sha=EXECUTION_SHA,
        git_root=preflight_v3.REPO_ROOT,
    )
    runtime_candidate.assert_called_once_with(
        context.spec,
        execution_sha=EXECUTION_SHA,
        source_root=preflight_v3.REPO_ROOT,
        git_root=preflight_v3.REPO_ROOT,
    )
    assert context.experiment_id == spec_v3.V3_EXPERIMENT_ID
    assert context.qualification_identity.startswith("ropv3q-")
    assert context.coverage is None


@pytest.mark.parametrize(
    ("command", "actual", "message"),
    [
        (("rev-parse", f"{EXECUTION_SHA}^{{commit}}"), "7" * 40, "commit"),
        (("rev-parse", f"{EXECUTION_SHA}^{{tree}}"), "8" * 40, "tree"),
    ],
)
def test_preflight_v3_rejects_wrong_execution_commit_or_tree(
    tmp_path: Path, command: tuple[str, str], actual: str, message: str
) -> None:
    corpus = tmp_path / "corpus"
    values = {
        ("rev-parse", f"{EXECUTION_SHA}^{{commit}}"): EXECUTION_SHA,
        ("rev-parse", f"{EXECUTION_SHA}^{{tree}}"): TREE_SHA,
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
            expected_manifest_sha256="3" * 64,
            expected_design_sha256="4" * 64,
            qualification_identity=_identity(manifest_sha="3" * 64, design_sha="4" * 64),
            execution_sha=EXECUTION_SHA,
            expected_tree_sha=TREE_SHA,
        )


def test_preflight_v3_rejects_manifest_design_binding_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inventory and role paths/hashes"):
        _run(tmp_path, manifest_design_sha="9" * 64)


def test_preflight_v3_rejects_extra_inventory(tmp_path: Path) -> None:
    corpus = tmp_path / "sealed"
    _write(corpus)
    (corpus / "extra.bin").write_bytes(b"extra")
    with pytest.raises(ValueError, match="filesystem inventory mismatch"):
        _run(tmp_path, corpus=corpus)


def test_preflight_v3_rejects_missing_inventory(tmp_path: Path) -> None:
    corpus = tmp_path / "sealed"
    _write(corpus)
    (corpus / "source" / "page-00.bin").unlink()
    with pytest.raises(ValueError, match="filesystem inventory mismatch"):
        _run(tmp_path, corpus=corpus)


def test_preflight_v3_rejects_symlink_path_escape_where_supported(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    corpus = tmp_path / "sealed"
    _write(corpus)
    source = corpus / "source" / "page-00.bin"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    try:
        source.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    with pytest.raises((ValueError, RuntimeError), match="symlink"):
        _run(tmp_path, corpus=corpus)


@pytest.mark.parametrize(
    ("payload", "message"),
    [(b"not a png", "PNG signature"), (_png(interlace=1), "non-interlaced 8-bit RGB")],
)
def test_preflight_v3_rejects_corrupt_or_non_rgb_png(
    tmp_path: Path, payload: bytes, message: str
) -> None:
    corpus = tmp_path / "sealed"
    _write(corpus)
    image_relative = "images/page-00.png"
    (corpus / image_relative).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"][0]["image"]["sha256"] = digest
    for record in manifest["inventory"]:
        if record["file"] == image_relative:
            record["sha256"] = digest
            record["bytes"] = len(payload)
    write_canonical_json(manifest_path, manifest)
    with pytest.raises(ValueError, match=message):
        _run(tmp_path, corpus=corpus)


def test_preflight_v3_rejects_manifest_file_hash_mismatch(tmp_path: Path) -> None:
    corpus = tmp_path / "sealed"
    _write(corpus)
    (corpus / "source" / "page-00.bin").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _run(tmp_path, corpus=corpus)


def test_preflight_v3_rejects_historical_content_hash(tmp_path: Path) -> None:
    corpus = tmp_path / "sealed"
    _write(corpus)
    reused = hashlib.sha256((corpus / "source" / "page-00.bin").read_bytes()).hexdigest()
    with (
        patch.object(historical_guard, "HISTORICAL_V2_HELDOUT_CONTENT_SHA256", frozenset({reused})),
        pytest.raises(ValueError, match="historical H01-H16"),
    ):
        _run(tmp_path, corpus=corpus)


def test_preflight_v3_rejects_retired_corpus_identity(tmp_path: Path) -> None:
    corpus = tmp_path / "sealed"
    _write(corpus)
    design_path = corpus / "corpus-design.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design["corpusId"] = RETIRED_CORPUS_ID
    write_canonical_json(design_path, design)
    write_manifest(corpus)
    with pytest.raises(ValueError, match="retired post-v2.*identity"):
        _run(tmp_path, corpus=corpus)


def test_preflight_v3_accepts_successful_canonical_corpus(tmp_path: Path) -> None:
    context, _validate_spec, _runtime_candidate, corpus = _run(tmp_path)
    assert context.experiment_id == spec_v3.V3_EXPERIMENT_ID
    assert (corpus / "manifest.json").is_file()


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
