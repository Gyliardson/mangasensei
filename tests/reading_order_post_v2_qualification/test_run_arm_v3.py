from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image
from scripts.reading_order_post_v2_qualification import run_arm_v3
from scripts.reading_order_post_v2_qualification.contracts import ArmId
from scripts.reading_order_v3_authoring import (
    AUTHORSHIP_BOUNDARY,
    DESIGN_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
)

from mangasensei.ocr.diagnostics import reading_order_post_v2_calibration as frozen_candidate
from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    CalibrationAssignment,
    CalibrationConfig,
    CalibrationDiagnostic,
    CalibrationRelationEdge,
    CalibrationResult,
)
from mangasensei.ocr.reading_order import PanelBox

PAGE_ID = "page.alpha"
EXECUTION_SHA = "a" * 40


def _quad(x1: int, y1: int, x2: int, y2: int) -> list[list[int]]:
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_clean_room_page(
    root: Path,
    *,
    design_page_id: str = PAGE_ID,
    input_page_id: str = PAGE_ID,
    input_path: str = "canonical/input-alpha.json",
    image_path: str = "canonical/image-alpha.png",
    image_mode: str = "RGB",
    image_size: tuple[int, int] = (12, 10),
) -> tuple[Path, Path]:
    design = {
        "schemaVersion": DESIGN_SCHEMA_VERSION,
        "corpusId": "small-unit-corpus",
        "version": "0.0.0-test",
        "authorshipBoundary": AUTHORSHIP_BOUNDARY,
        "provenanceDeclaration": {
            "priorHeldoutEvidenceInspected": False,
            "calibrationOutputsInspected": False,
            "candidateDiagnosticsInspected": False,
            "candidateExecuted": False,
            "qualificationExecuted": False,
            "annotationsAdaptedToCandidateOutput": False,
        },
        "pages": [
            {
                "pageId": design_page_id,
                "source": "traps/source-do-not-read.bin",
                "image": image_path,
                "input": input_path,
                "annotation": "traps/annotation-do-not-read.json",
                "authoringCoverage": {
                    "positiveFamilies": [],
                    "primaryPositiveFamily": None,
                    "c3Rejection": False,
                },
            }
        ],
    }
    (root / "corpus-design.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "corpus-design.json").write_text(json.dumps(design), encoding="utf-8")

    resolved_input = root / input_path
    resolved_input.parent.mkdir(parents=True, exist_ok=True)
    resolved_input.write_text(
        json.dumps(
            {
                "schemaVersion": INPUT_SCHEMA_VERSION,
                "pageId": input_page_id,
                "width": 12,
                "height": 10,
                "regions": [
                    {
                        "regionId": "region-second",
                        "sourceIndex": 1,
                        "lines": [_quad(7, 2, 10, 5)],
                        "angle": -3,
                    },
                    {
                        "regionId": "region-first",
                        "sourceIndex": 0,
                        "lines": [_quad(1, 2, 4, 5)],
                        "angle": 7,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    resolved_image = root / image_path
    resolved_image.parent.mkdir(parents=True, exist_ok=True)
    shape = (image_size[1], image_size[0], 3) if image_mode == "RGB" else image_size[::-1]
    Image.fromarray(np.zeros(shape, dtype=np.uint8)).save(resolved_image)
    manifest = {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "corpusId": design["corpusId"],
        "version": design["version"],
        "design": {"file": "corpus-design.json", "sha256": _sha256(root / "corpus-design.json")},
        "pages": [
            {
                "pageId": design_page_id,
                "source": {"file": "traps/source-do-not-read.bin", "sha256": "1" * 64},
                "image": {"file": image_path, "sha256": _sha256(resolved_image)},
                "input": {"file": input_path, "sha256": _sha256(resolved_input)},
                "annotation": {
                    "file": "traps/annotation-do-not-read.json",
                    "sha256": "2" * 64,
                },
            }
        ],
        "inventory": [],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return resolved_input, resolved_image


def _result(
    regions: tuple[Any, ...],
    *,
    order: tuple[int, ...] = (1, 0),
    diagnostic_order: tuple[str, ...] | None = None,
) -> CalibrationResult:
    ordered = tuple(regions[index] for index in order)
    actual_order = tuple(ref.region_id for ref in ordered)
    assignments = tuple(
        CalibrationAssignment(
            region_id=ref.region_id,
            candidate_group_indices=(index,),
            status="assigned",
            reason="unit-boundary",
            assigned_group_index=index,
        )
        for index, ref in enumerate(regions)
    )
    diagnostic = CalibrationDiagnostic(
        segmentation_boxes=(PanelBox(0, 0, 6, 10), PanelBox(6, 0, 12, 10)),
        segmentation_reliable=True,
        segmentation_reason="reliable",
        recovery_reason="not-attempted",
        assignments=assignments,
        relation_edges=(CalibrationRelationEdge("g000", "g001", "unit-edge"),),
        node_order=("g000", "g001"),
        fallback_reason=None,
        used_panel_evidence=True,
        fallback_order=("region-first", "region-second"),
        final_order=diagnostic_order if diagnostic_order is not None else actual_order,
    )
    return CalibrationResult(ordered_regions=ordered, diagnostic=diagnostic)


def _install_candidate(
    monkeypatch: pytest.MonkeyPatch,
    factory: Callable[[tuple[Any, ...]], CalibrationResult],
) -> list[tuple[np.ndarray, tuple[Any, ...], int, CalibrationConfig]]:
    calls: list[tuple[np.ndarray, tuple[Any, ...], int, CalibrationConfig]] = []

    def candidate(
        pixels: np.ndarray,
        regions: tuple[Any, ...],
        *,
        page_height: int,
        config: CalibrationConfig,
    ) -> CalibrationResult:
        calls.append((pixels, regions, page_height, config))
        return factory(regions)

    monkeypatch.setattr(run_arm_v3, "_verify_candidate_origin", lambda: candidate)
    return calls


def _execute(root: Path, output: Path) -> tuple[Path, Path]:
    return run_arm_v3.execute_page(
        corpus_root=root,
        page_id=PAGE_ID,
        arm_id=ArmId.C1_C2_C3_B1,
        execution_sha=EXECUTION_SHA,
        repeat=2,
        output_root=output,
    )


def test_executes_direct_verified_candidate_and_preserves_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    input_path, image_path = _write_clean_room_page(root)
    calls = _install_candidate(monkeypatch, _result)

    diagnostic_path, ordering_path = _execute(root, tmp_path / "output")

    assert inspect.signature(run_arm_v3.execute_page).parameters.keys() == {
        "corpus_root",
        "page_id",
        "arm_id",
        "execution_sha",
        "repeat",
        "output_root",
    }
    assert len(calls) == 1
    pixels, regions, page_height, config = calls[0]
    assert pixels.dtype == np.uint8
    assert pixels.shape == (10, 12, 3)
    assert page_height == 10
    assert (config.c1_boundary_guard, config.c2_uncertain_relations) == (True, True)
    assert (config.c3_merged_frame_recovery, config.b1_local_order) == (True, True)
    assert tuple(ref.region_id for ref in regions) == ("region-first", "region-second")
    assert tuple(ref.source_index for ref in regions) == (0, 1)
    assert tuple(ref.region.text for ref in regions) == (
        "qualification-fixture",
        "qualification-fixture",
    )

    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    ordering = json.loads(ordering_path.read_text(encoding="utf-8"))
    assert diagnostic_path == (
        tmp_path
        / "output"
        / "raw"
        / ArmId.C1_C2_C3_B1.value
        / "repeat-2"
        / f"{PAGE_ID}.diagnostic.json"
    )
    assert ordering["finalOrder"] == ["region-second", "region-first"]
    assert diagnostic["finalOrder"] == ordering["finalOrder"]
    assert diagnostic["assignments"][0]["sourceIndex"] == 0
    assert diagnostic["assignments"][1]["sourceIndex"] == 1
    assert diagnostic["regionIntegrity"] == {
        "countPreserved": True,
        "objectIdentitySetPreserved": True,
        "contentConfidenceGeometryPreserved": True,
    }
    assert input_path != root / "inputs" / f"{PAGE_ID}.json"
    assert image_path != root / "images" / f"{PAGE_ID}.png"


def test_does_not_read_annotation_or_ground_truth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)
    _install_candidate(monkeypatch, _result)
    reads: list[Path] = []
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes

    def tracked_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(path)
        if "annotation" in path.name or "ground" in path.name.lower():
            raise AssertionError("annotation/GT trap was read")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read_text)

    def tracked_read_bytes(path: Path) -> bytes:
        reads.append(path)
        if "annotation" in path.name or "ground" in path.name.lower():
            raise AssertionError("annotation/GT trap was read")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)
    _execute(root, tmp_path / "output")
    assert not any("annotation" in path.name or "ground" in path.name.lower() for path in reads)
    assert root / "canonical/input-alpha.json" in reads


def test_candidate_origin_executes_authenticated_head_bytes_in_fresh_namespace() -> None:
    assert run_arm_v3.CANDIDATE_PATH == (
        run_arm_v3.REPO_ROOT
        / "backend"
        / "src"
        / "mangasensei"
        / "ocr"
        / "diagnostics"
        / "reading_order_post_v2_calibration.py"
    )
    assert Path(frozen_candidate.__file__).resolve() == run_arm_v3.CANDIDATE_PATH
    authenticated = run_arm_v3._verify_candidate_origin()
    assert authenticated is not frozen_candidate.run_post_v2_calibration_candidate
    assert authenticated.__globals__ is not vars(frozen_candidate)
    assert authenticated.__name__ == "run_post_v2_calibration_candidate"
    assert authenticated.__module__ != frozen_candidate.__name__
    assert authenticated.__globals__["__package__"] == frozen_candidate.__package__
    assert authenticated.__globals__["__file__"] == str(run_arm_v3.CANDIDATE_PATH)


@pytest.mark.parametrize("mismatch", ["source"])
def test_rejects_candidate_origin_mismatch(
    mismatch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if mismatch == "source":
        monkeypatch.setattr(frozen_candidate, "__file__", str(tmp_path / "impostor.py"))
    with pytest.raises(RuntimeError, match="frozen candidate"):
        run_arm_v3._verify_candidate_origin()


@pytest.mark.parametrize("tamper", ["function-code", "module-callable", "stale-loader"])
def test_imported_candidate_and_loader_mutation_cannot_change_authenticated_invocation(
    tamper: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)

    def substituted(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("mutable imported candidate was invoked")

    if tamper == "function-code":
        monkeypatch.setattr(
            frozen_candidate.run_post_v2_calibration_candidate,
            "__code__",
            substituted.__code__,
        )
    elif tamper == "module-callable":
        monkeypatch.setattr(
            frozen_candidate, "run_post_v2_calibration_candidate", substituted
        )
    else:
        monkeypatch.setattr(
            frozen_candidate,
            "__spec__",
            SimpleNamespace(loader=SimpleNamespace(get_code=lambda _name: substituted.__code__)),
        )

    _diagnostic, ordering = _execute(root, tmp_path / "output")
    assert ordering.is_file()


@pytest.mark.parametrize("page_id", ["../escape", "nested/page", "bad page", "a" * 129])
def test_rejects_unsafe_page_id_before_output_construction(
    page_id: str, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="safe page ID"):
        run_arm_v3.execute_page(
            corpus_root=tmp_path / "missing",
            page_id=page_id,
            arm_id=ArmId.CONTROL,
            execution_sha=EXECUTION_SHA,
            repeat=1,
            output_root=tmp_path / "output",
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("role", ["input", "image"])
def test_rejects_manifest_role_hash_mismatch(role: str, tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"][0][role]["sha256"] = "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=f"manifest.*{role}.*SHA-256"):
        _execute(root, tmp_path / "output")


@pytest.mark.parametrize("mismatch", ["identity", "role-record"])
def test_rejects_inexact_manifest_identity_and_role_records(
    mismatch: str, tmp_path: Path
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mismatch == "identity":
        manifest["corpusId"] = "different-corpus"
        message = "corpus identity"
    else:
        manifest["pages"][0]["input"]["extra"] = True
        message = "exact property set"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        _execute(root, tmp_path / "output")


@pytest.mark.parametrize("stage", ["input", "image"])
def test_rechecks_manifest_hash_after_load_and_decode(
    stage: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    input_path, image_path = _write_clean_room_page(root)
    _install_candidate(monkeypatch, _result)
    if stage == "input":
        real_load = run_arm_v3.load_arm_input

        def replacing_input(path: Path) -> Any:
            page = real_load(path)
            input_path.write_bytes(input_path.read_bytes() + b" ")
            return page

        monkeypatch.setattr(run_arm_v3, "load_arm_input", replacing_input)
    else:
        real_decode = run_arm_v3._decode_rgb_image

        def replacing_image(path: Path, **kwargs: Any) -> np.ndarray:
            pixels = real_decode(path, **kwargs)
            image_path.write_bytes(image_path.read_bytes() + b"trailing-substitution")
            return pixels

        monkeypatch.setattr(run_arm_v3, "_decode_rgb_image", replacing_image)
    with pytest.raises(ValueError, match=f"manifest.*{stage}.*SHA-256"):
        _execute(root, tmp_path / "output")


def test_rejects_symlinked_root_and_asset_components(
    tmp_path: Path,
) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    root = tmp_path / "corpus"
    _write_clean_room_page(root)
    root_link = tmp_path / "corpus-link"
    try:
        root_link.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="symlink"):
        run_arm_v3._canonical_asset_path(root_link, "canonical/input-alpha.json", role="input")

    canonical = root / "canonical"
    real_assets = root / "real-assets"
    canonical.rename(real_assets)
    canonical.symlink_to(real_assets, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run_arm_v3._canonical_asset_path(root, "canonical/input-alpha.json", role="input")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing-page", "requested page"),
        ("input-page", "input pageId mismatch"),
        ("unsafe-input", "safe normalized POSIX relative path"),
    ],
)
def test_rejects_page_and_canonical_path_mismatches(
    case: str, message: str, tmp_path: Path
) -> None:
    root = tmp_path / "corpus"
    if case == "missing-page":
        _write_clean_room_page(root, design_page_id="different-page")
    elif case == "input-page":
        _write_clean_room_page(root, input_page_id="different-page")
    else:
        _write_clean_room_page(root, input_path="../escaped-input.json")
    with pytest.raises(ValueError, match=message):
        _execute(root, tmp_path / "output")


@pytest.mark.parametrize(
    ("mode", "size", "message"),
    [
        ("L", (12, 10), "RGB"),
        ("RGB", (11, 10), "dimensions"),
    ],
)
def test_rejects_non_rgb_or_dimension_mismatched_image(
    mode: str, size: tuple[int, int], message: str, tmp_path: Path
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root, image_mode=mode, image_size=size)
    with pytest.raises(ValueError, match=message):
        _execute(root, tmp_path / "output")


@pytest.mark.parametrize(
    ("pixels", "message"),
    [
        (np.zeros((10, 12, 3), dtype=np.float32), "uint8"),
        (np.zeros((10, 12), dtype=np.uint8), "HxWx3"),
        (np.zeros((10, 12, 4), dtype=np.uint8), "HxWx3"),
    ],
)
def test_rejects_decoder_dtype_and_shape(
    pixels: np.ndarray, message: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)
    monkeypatch.setattr(run_arm_v3, "_array_from_rgb_image", lambda _image: pixels)
    with pytest.raises(ValueError, match=message):
        _execute(root, tmp_path / "output")


@pytest.mark.parametrize("violation", ["count", "object", "id", "sourceIndex"])
def test_rejects_candidate_count_object_and_id_invariant_violations(
    violation: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)

    def violating(regions: tuple[Any, ...]) -> CalibrationResult:
        result = _result(regions)
        if violation == "count":
            return CalibrationResult(result.ordered_regions[:1], result.diagnostic)
        if violation == "object":
            duplicate = (regions[0], regions[0])
            return CalibrationResult(duplicate, result.diagnostic)
        if violation == "sourceIndex":
            ordered_result = _result(regions, order=(0, 1))
            replacement = type(regions[0])(
                regions[0].region_id,
                99,
                regions[0].region,
            )
            return CalibrationResult(
                (replacement, regions[1]),
                ordered_result.diagnostic,
            )
        replacement = type(regions[0])("different-id", regions[0].source_index, regions[0].region)
        return CalibrationResult((replacement, regions[1]), result.diagnostic)

    _install_candidate(monkeypatch, violating)
    with pytest.raises(AssertionError, match=violation):
        _execute(root, tmp_path / "output")


def test_rejects_any_candidate_mutation_from_full_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)

    def mutating(regions: tuple[Any, ...]) -> CalibrationResult:
        regions[0].region.translation = "mutated-hidden-field"
        return _result(regions)

    _install_candidate(monkeypatch, mutating)
    with pytest.raises(AssertionError, match="modified frozen input region"):
        _execute(root, tmp_path / "output")


def test_rejects_ordering_and_diagnostic_disagreement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)
    _install_candidate(
        monkeypatch,
        lambda regions: _result(
            regions,
            order=(1, 0),
            diagnostic_order=("region-first", "region-second"),
        ),
    )
    with pytest.raises(AssertionError, match="final order"):
        _execute(root, tmp_path / "output")


def test_cli_is_equivalent_to_execute_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    _write_clean_room_page(root)
    _install_candidate(monkeypatch, _result)
    output = tmp_path / "cli-output"
    run_arm_v3.main(
        [
            "--corpus-root",
            str(root),
            "--page-id",
            PAGE_ID,
            "--arm",
            ArmId.C1_C2_C3_B1.value,
            "--execution-sha",
            EXECUTION_SHA,
            "--repeat",
            "2",
            "--output-root",
            str(output),
        ]
    )
    ordering = json.loads(
        (
            output
            / "raw"
            / ArmId.C1_C2_C3_B1.value
            / "repeat-2"
            / f"{PAGE_ID}.ordering.json"
        ).read_text(encoding="utf-8")
    )
    assert ordering["finalOrder"] == ["region-second", "region-first"]
