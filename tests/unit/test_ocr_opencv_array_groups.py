from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mangasensei.ocr.diagnostics import opencv_ab as opencv_ab_module
from mangasensei.ocr.diagnostics.opencv_ab import (
    compare_probe_roots,
    semantic_equivalence_failures,
)
from mangasensei.ocr.diagnostics.opencv_artifacts import (
    write_fixture_artifact,
    write_probe_manifest,
)


def test_duplicate_array_geometry_compares_as_an_order_independent_group() -> None:
    baseline, candidate, baseline_arrays, candidate_arrays = _duplicate_evidence()

    comparison = opencv_ab_module._compare_array_backed_spatial_stage(
        baseline, candidate, baseline_arrays, candidate_arrays
    )

    assert comparison["matched_count"] == 2
    assert comparison["unmatched_count"] == 0
    assert comparison["inference_context_change_count"] == 0
    assert comparison["matches"][0]["baseline_multiplicity"] == 2
    assert comparison["matches"][0]["delta"]["changed_values"] == 1


def test_inference_context_drift_reaches_the_semantic_gate() -> None:
    baseline, candidate, baseline_arrays, candidate_arrays = _duplicate_evidence()
    changed_candidate = (
        candidate[0],
        {**candidate[1], "inference_call": 2},
    )

    comparison = opencv_ab_module._compare_array_backed_spatial_stage(
        baseline, changed_candidate, baseline_arrays, candidate_arrays
    )
    gate_payload = {key: 0 for key in opencv_ab_module._SEMANTIC_EQUIVALENCE_COUNTS}

    assert comparison["inference_context_change_count"] == 1
    assert semantic_equivalence_failures(
        {**gate_payload, "recognizer_inference_context_change_count": 1}
    ) == {"recognizer_inference_context_change_count": 1}


def test_inference_context_drift_aggregates_through_probe_comparison(
    tmp_path: Path,
) -> None:
    baseline_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline, candidate, baseline_arrays, candidate_arrays = _duplicate_evidence()
    changed_candidate = (candidate[0], {**candidate[1], "inference_call": 2})
    _write_probe(baseline_root, "4.14.0.94", baseline, baseline_arrays)
    _write_probe(candidate_root, "5.0.0.93", changed_candidate, candidate_arrays)

    comparison = compare_probe_roots(baseline_root, candidate_root)

    assert comparison["recognizer_inference_context_change_count"] == 1
    assert semantic_equivalence_failures(comparison) == {
        "recognizer_inference_context_change_count": 1
    }


def test_duplicate_geometry_rejects_different_within_probe_arrays() -> None:
    baseline, candidate, baseline_arrays, candidate_arrays = _duplicate_evidence()
    divergent_baseline = {
        **baseline_arrays,
        "baseline_1": np.asarray([[9]], dtype=np.uint8),
    }

    with pytest.raises(ValueError, match="different stage values"):
        opencv_ab_module._compare_array_backed_spatial_stage(
            baseline, candidate, divergent_baseline, candidate_arrays
        )


def _duplicate_evidence() -> tuple[
    tuple[dict[str, object], ...],
    tuple[dict[str, object], ...],
    dict[str, np.ndarray],
    dict[str, np.ndarray],
]:
    baseline = (
        {
            "bbox": (0.0, 0.0, 10.0, 10.0),
            "array_key": "baseline_0",
            "inference_call": 0,
            "width": 10,
        },
        {
            "bbox": (0.0, 0.0, 10.0, 10.0),
            "array_key": "baseline_1",
            "inference_call": 1,
            "width": 10,
        },
    )
    candidate = (
        {
            "bbox": (0.0, 0.0, 10.0, 10.0),
            "array_key": "candidate_1",
            "inference_call": 1,
            "width": 10,
        },
        {
            "bbox": (0.0, 0.0, 10.0, 10.0),
            "array_key": "candidate_0",
            "inference_call": 0,
            "width": 10,
        },
    )
    return (
        baseline,
        candidate,
        {
            "baseline_0": np.asarray([[1]], dtype=np.uint8),
            "baseline_1": np.asarray([[1]], dtype=np.uint8),
        },
        {
            "candidate_0": np.asarray([[2]], dtype=np.uint8),
            "candidate_1": np.asarray([[2]], dtype=np.uint8),
        },
    )


def _write_probe(
    root: Path,
    opencv_distribution: str,
    inputs: tuple[dict[str, object], ...],
    arrays: dict[str, np.ndarray],
) -> None:
    fixture = write_fixture_artifact(
        root,
        fixture_file="v01/synthetic.jpg",
        record={
            "image_sha256": "fixture-sha",
            "detector": {"candidates": []},
            "recognizer": {"crops": [], "inputs": list(inputs), "accepted": []},
            "merge": {"regions": []},
            "final_regions": [],
            "array_stages": {},
        },
        arrays=arrays,
    )
    write_probe_manifest(
        root,
        metadata={
            "schema_version": 1,
            "repository_sha": "same-code-sha",
            "opencv": {
                "distribution_version": opencv_distribution,
                "runtime_version": opencv_distribution.rsplit(".", 1)[0],
                "build_information_sha256": f"build-{opencv_distribution}",
                "thread_count": 4,
                "optimized": True,
            },
            "runtime": {"python": "3.11.9", "numpy": "2.4.6"},
            "fixture_count": 1,
            "fixture_manifest_sha256": "fixture-manifest-sha",
            "model_manifest_sha256": "model-manifest-sha",
            "ocr_config_digest": "ocr-config-digest",
        },
        fixtures=(fixture,),
    )
