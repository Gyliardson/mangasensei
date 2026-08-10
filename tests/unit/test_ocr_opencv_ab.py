from __future__ import annotations

import copy
import importlib
import runpy
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from mangasensei.ocr.diagnostics.opencv_ab import (
    compare_probe_roots,
    safe_comparison_summary,
    semantic_equivalence_failures,
)
from mangasensei.ocr.diagnostics.opencv_artifacts import (
    validate_artifact_root,
    write_fixture_artifact,
    write_fixture_notice,
    write_probe_manifest,
)
from mangasensei.ocr.diagnostics.opencv_matching import (
    array_delta,
    array_descriptor,
    array_fingerprint,
    source_zone_statistics,
    spatially_match,
)


def test_spatial_match_uses_geometry_instead_of_candidate_order() -> None:
    baseline = (
        {"bbox": (10.0, 10.0, 30.0, 50.0)},
        {"bbox": (80.0, 20.0, 120.0, 70.0)},
    )
    candidate = (
        {"bbox": (79.0, 21.0, 121.0, 69.0)},
        {"bbox": (11.0, 9.0, 31.0, 51.0)},
    )

    result = spatially_match(baseline, candidate)

    assert tuple((match.baseline_index, match.candidate_index) for match in result.matches) == (
        (0, 1),
        (1, 0),
    )
    assert result.unmatched_baseline == ()
    assert result.unmatched_candidate == ()


def test_spatial_match_reports_candidates_without_plausible_overlap() -> None:
    baseline = ({"bbox": (10.0, 10.0, 30.0, 50.0)},)
    candidate = ({"bbox": (400.0, 500.0, 440.0, 560.0)},)

    result = spatially_match(baseline, candidate)

    assert result.matches == ()
    assert result.unmatched_baseline == (0,)
    assert result.unmatched_candidate == (0,)


def test_spatial_match_rejects_touching_or_extreme_area_pairs() -> None:
    baseline = ({"bbox": (0.0, 0.0, 10.0, 10.0)},)

    touching = spatially_match(baseline, ({"bbox": (10.0, 0.0, 20.0, 10.0)},))
    extreme_area = spatially_match(
        ({"bbox": (0.0, 0.0, 100.0, 100.0)},),
        ({"bbox": (99.0, 99.0, 101.0, 101.0)},),
    )

    assert touching.matches == ()
    assert extreme_area.matches == ()


def test_spatial_match_maximizes_valid_pair_count_before_overlap() -> None:
    baseline = (
        {"bbox": (0.0, 0.0, 10.0, 10.0)},
        {"bbox": (8.0, 0.0, 18.0, 10.0)},
    )
    candidate = (
        {"bbox": (3.0, 0.0, 13.0, 10.0)},
        {"bbox": (-5.0, 0.0, 5.0, 10.0)},
    )

    result = spatially_match(baseline, candidate)

    assert tuple((match.baseline_index, match.candidate_index) for match in result.matches) == (
        (0, 1),
        (1, 0),
    )


def test_spatial_match_selects_global_weight_optimum() -> None:
    baseline = (
        {"bbox": (0.0, 0.0, 4.0, 10.0)},
        {"bbox": (0.0, 0.0, 9.0, 10.0)},
    )
    candidate = (
        {"bbox": (0.0, 0.0, 6.0, 10.0)},
        {"bbox": (1.0, 0.0, 5.0, 10.0)},
    )

    result = spatially_match(baseline, candidate)

    assert tuple((match.baseline_index, match.candidate_index) for match in result.matches) == (
        (0, 1),
        (1, 0),
    )


def test_spatial_match_rejects_geometry_only_ties() -> None:
    duplicate_geometry = (
        {"bbox": (0.0, 0.0, 10.0, 10.0)},
        {"bbox": (0.0, 0.0, 10.0, 10.0)},
    )

    with pytest.raises(ValueError, match="ambiguous spatial matching"):
        spatially_match(duplicate_geometry, tuple(reversed(duplicate_geometry)))


def test_spatial_match_tie_break_is_independent_of_candidate_order() -> None:
    baseline = (
        {"bbox": (0.0, 0.0, 10.0, 10.0)},
        {"bbox": (10.0, 0.0, 20.0, 10.0)},
    )
    candidate = (
        {"bbox": (5.0, -1.0, 15.0, 9.0)},
        {"bbox": (5.0, 1.0, 15.0, 11.0)},
    )

    forward = spatially_match(baseline, candidate)
    reversed_candidates = tuple(reversed(candidate))
    reversed_result = spatially_match(baseline, reversed_candidates)
    forward_geometry = {
        (baseline[match.baseline_index]["bbox"], candidate[match.candidate_index]["bbox"])
        for match in forward.matches
    }
    reversed_geometry = {
        (
            baseline[match.baseline_index]["bbox"],
            reversed_candidates[match.candidate_index]["bbox"],
        )
        for match in reversed_result.matches
    }

    assert reversed_geometry == forward_geometry


def test_array_delta_characterizes_numeric_change_without_pixel_exact_policy() -> None:
    baseline = np.asarray([[0.0, 1.0], [2.0, 3.0]], dtype=np.float32)
    candidate = np.asarray([[0.0, 1.25], [1.5, 3.0]], dtype=np.float32)

    result = array_delta(baseline, candidate)

    assert result["shape_equal"] is True
    assert result["changed_values"] == 2
    assert result["changed_fraction"] == pytest.approx(0.5)
    assert result["max_absolute_delta"] == pytest.approx(0.5)
    assert result["mean_absolute_delta"] == pytest.approx(0.1875)


def test_array_fingerprint_includes_shape_and_dtype() -> None:
    flat = np.asarray([1, 2, 3, 4], dtype=np.uint8)
    matrix = flat.reshape(2, 2)
    wider_type = flat.astype(np.uint16)

    assert array_fingerprint(flat) != array_fingerprint(matrix)
    assert array_fingerprint(flat) != array_fingerprint(wider_type)


def test_array_descriptor_records_stable_aggregate_evidence() -> None:
    values = np.asarray([[1, 2], [3, 4]], dtype=np.uint8)

    descriptor = array_descriptor(values)

    assert descriptor == {
        "shape": [2, 2],
        "dtype": "|u1",
        "fingerprint": array_fingerprint(values),
        "minimum": 1.0,
        "maximum": 4.0,
        "mean": 2.5,
    }


def test_source_zone_statistics_maps_source_pixels_into_raw_detector_map() -> None:
    first_channel = np.arange(64 * 32, dtype=np.float32).reshape(64, 32)
    probability_map = np.stack((first_channel, first_channel + 10_000))[None, ...]

    statistics = source_zone_statistics(
        probability_map,
        source_width=100,
        source_height=200,
        resize_ratio=0.5,
        detector_input_width=64,
        detector_input_height=128,
        source_zone=(20, 40, 40, 80),
    )

    expected = probability_map[0, 0, 10:20, 5:10]
    assert statistics["map_bounds"] == [5, 10, 10, 20]
    assert statistics["value_count"] == 50
    assert statistics["minimum"] == pytest.approx(float(expected.min()))
    assert statistics["maximum"] == pytest.approx(float(expected.max()))
    assert statistics["mean"] == pytest.approx(float(expected.mean()))


def test_artifact_root_must_stay_under_ignored_diagnostic_directory(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    expected = repository / "var" / "ocr-opencv-ab" / "opencv-4"

    assert validate_artifact_root(expected, repository_root=repository) == expected.resolve()

    with pytest.raises(ValueError, match="var/ocr-opencv-ab"):
        validate_artifact_root(repository / "diagnostics", repository_root=repository)


def test_fixture_artifact_rejects_windows_or_posix_traversal(tmp_path: Path) -> None:
    root = tmp_path / "var" / "ocr-opencv-ab" / "probe"

    for fixture_file in ("../outside.jpg", "..\\outside.jpg", "C:\\outside.jpg"):
        with pytest.raises(ValueError, match="safe relative"):
            write_fixture_artifact(
                root,
                fixture_file=fixture_file,
                record=_fixture_record(),
                arrays={},
            )


def test_console_summary_never_contains_ocr_text() -> None:
    comparison = {
        "baseline_opencv": "4.14.0",
        "candidate_opencv": "5.0.0",
        "fixture_count": 12,
        "semantic_text_change_count": 1,
        "text_changes": [
            {
                "baseline_text": "licensed baseline transcript",
                "candidate_text": "licensed candidate transcript",
            }
        ],
        "unmatched_detector_candidates": 2,
        "unmatched_final_regions": 0,
    }

    summary = safe_comparison_summary(comparison)

    assert "licensed baseline transcript" not in summary
    assert "licensed candidate transcript" not in summary
    assert "semantic_text_change_count=1" in summary


def test_probe_artifacts_compare_spatial_stages_and_numeric_arrays(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    baseline_root = validate_artifact_root(
        repository / "var" / "ocr-opencv-ab" / "opencv-4",
        repository_root=repository,
    )
    candidate_root = validate_artifact_root(
        repository / "var" / "ocr-opencv-ab" / "opencv-5",
        repository_root=repository,
    )
    baseline_record = _fixture_record(
        detector_candidates=[
            {"bbox": [10, 10, 30, 50], "score": 0.9},
            {"bbox": [80, 20, 120, 70], "score": 0.8},
        ],
        crop_bbox=[10, 10, 30, 50],
        final_text="stable transcript",
    )
    candidate_record = _fixture_record(
        detector_candidates=[
            {"bbox": [79, 21, 121, 69], "score": 0.79},
            {"bbox": [11, 9, 31, 51], "score": 0.91},
        ],
        crop_bbox=[11, 9, 31, 51],
        final_text="stable transcript",
    )

    baseline_entry = write_fixture_artifact(
        baseline_root,
        fixture_file="v01/synthetic.jpg",
        record=baseline_record,
        arrays={
            "detector_db": np.asarray([[0.1, 0.2]], dtype=np.float32),
            "recognizer_crop_000": np.asarray([[10, 20]], dtype=np.uint8),
        },
    )
    candidate_entry = write_fixture_artifact(
        candidate_root,
        fixture_file="v01/synthetic.jpg",
        record=candidate_record,
        arrays={
            "detector_db": np.asarray([[0.1, 0.25]], dtype=np.float32),
            "recognizer_crop_000": np.asarray([[10, 21]], dtype=np.uint8),
        },
    )
    write_probe_manifest(
        baseline_root,
        metadata=_probe_metadata("4.14.0.94"),
        fixtures=(baseline_entry,),
    )
    write_probe_manifest(
        candidate_root,
        metadata=_probe_metadata("5.0.0.93"),
        fixtures=(candidate_entry,),
    )

    comparison = compare_probe_roots(baseline_root, candidate_root)

    assert comparison["fixture_count"] == 1
    assert comparison["recognizer_text_change_count"] == 0
    assert comparison["merge_text_change_count"] == 0
    assert comparison["semantic_text_change_count"] == 0
    assert comparison["unmatched_detector_candidates"] == 0
    assert comparison["unmatched_final_regions"] == 0
    assert comparison["final_bbox_change_count"] == 1
    assert comparison["final_polygon_value_change_count"] == 1
    assert semantic_equivalence_failures(comparison) == {
        "final_bbox_change_count": 1,
        "final_polygon_value_change_count": 1,
    }
    assert semantic_equivalence_failures(
        {
            **comparison,
            "final_bbox_change_count": 0,
            "final_polygon_value_change_count": 0,
            "semantic_text_change_count": 1,
        }
    ) == {
        "semantic_text_change_count": 1,
    }
    fixture_comparison = comparison["fixtures"][0]
    assert fixture_comparison["detector"]["matched_count"] == 2
    assert fixture_comparison["recognizer_accepted"]["matched_count"] == 1
    assert fixture_comparison["merge"]["matched_count"] == 1
    assert fixture_comparison["final_regions"]["reading_order_change_count"] == 0
    assert fixture_comparison["arrays"]["detector_db"]["changed_values"] == 1
    assert fixture_comparison["recognizer_crops"]["unmatched_count"] == 0
    assert fixture_comparison["recognizer_crops"]["matches"][0]["delta"]["changed_values"] == 1
    assert fixture_comparison["recognizer_inputs"]["unmatched_count"] == 0
    assert fixture_comparison["recognizer_inputs"]["matches"][0]["delta"]["changed_values"] == 1
    with pytest.raises(ValueError, match="current clean checkout"):
        compare_probe_roots(
            baseline_root,
            candidate_root,
            expected_repository_sha="different-repository-sha",
        )


def test_probe_comparison_rejects_different_source_or_model_inputs(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    baseline_root = repository / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = repository / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_entry = write_fixture_artifact(
        baseline_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays=_fixture_arrays(),
    )
    candidate_entry = write_fixture_artifact(
        candidate_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays=_fixture_arrays(),
    )
    write_probe_manifest(
        baseline_root,
        metadata=_probe_metadata("4.14.0.94"),
        fixtures=(baseline_entry,),
    )
    changed_metadata = {**_probe_metadata("5.0.0.93"), "model_manifest_sha256": "changed"}
    write_probe_manifest(candidate_root, metadata=changed_metadata, fixtures=(candidate_entry,))

    with pytest.raises(ValueError, match="model_manifest_sha256"):
        compare_probe_roots(baseline_root, candidate_root)


def test_probe_comparison_rejects_runtime_drift_and_tampered_artifacts(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    baseline_root = repository / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = repository / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_entry = write_fixture_artifact(
        baseline_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays=_fixture_arrays(),
    )
    candidate_entry = write_fixture_artifact(
        candidate_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays=_fixture_arrays(),
    )
    write_probe_manifest(
        baseline_root,
        metadata=_probe_metadata("4.14.0.94"),
        fixtures=(baseline_entry,),
    )
    changed_runtime = {
        **_probe_metadata("5.0.0.93"),
        "runtime": {"python": "3.11.9", "numpy": "different"},
    }
    write_probe_manifest(candidate_root, metadata=changed_runtime, fixtures=(candidate_entry,))

    with pytest.raises(ValueError, match="runtime"):
        compare_probe_roots(baseline_root, candidate_root)

    write_probe_manifest(
        candidate_root,
        metadata=_probe_metadata("5.0.0.93"),
        fixtures=(candidate_entry,),
    )
    candidate_record = candidate_root / candidate_entry["record"]
    candidate_record.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact checksum"):
        compare_probe_roots(baseline_root, candidate_root)


def test_schema_two_requires_complete_integrity_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    baseline_root = repository / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = repository / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_entry = write_fixture_artifact(
        baseline_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays=_fixture_arrays(),
    )
    candidate_entry = write_fixture_artifact(
        candidate_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays=_fixture_arrays(),
    )
    write_probe_manifest(
        baseline_root,
        metadata=_schema_two_probe_metadata("4.14.0.94"),
        fixtures=(baseline_entry,),
    )
    write_probe_manifest(
        candidate_root,
        metadata=_schema_two_probe_metadata("5.0.0.93"),
        fixtures=(candidate_entry,),
    )

    assert compare_probe_roots(baseline_root, candidate_root)["fixture_count"] == 1

    incomplete_entry = {
        key: value for key, value in candidate_entry.items() if key != "arrays_sha256"
    }
    write_probe_manifest(
        candidate_root,
        metadata=_schema_two_probe_metadata("5.0.0.93"),
        fixtures=(incomplete_entry,),
    )
    with pytest.raises(ValueError, match="missing required paths"):
        compare_probe_roots(baseline_root, candidate_root)


def test_probe_manifest_rejects_empty_fixture_evidence(tmp_path: Path) -> None:
    baseline_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-5"
    write_probe_manifest(
        baseline_root,
        metadata=_probe_metadata("4.14.0.94"),
        fixtures=(),
    )
    write_probe_manifest(
        candidate_root,
        metadata=_probe_metadata("5.0.0.93"),
        fixtures=(),
    )

    with pytest.raises(ValueError, match="fixture_count"):
        compare_probe_roots(baseline_root, candidate_root)


def test_probe_comparison_reports_final_angle_and_polygon_presence_changes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    baseline_root = repository / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = repository / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_record = _fixture_record()
    candidate_record = copy.deepcopy(baseline_record)
    candidate_final = candidate_record["final_regions"][0]
    candidate_final["angle"] = 1.0
    candidate_final["polygon"] = None
    baseline_entry = write_fixture_artifact(
        baseline_root,
        fixture_file="v01/synthetic.jpg",
        record=baseline_record,
        arrays=_fixture_arrays(),
    )
    candidate_entry = write_fixture_artifact(
        candidate_root,
        fixture_file="v01/synthetic.jpg",
        record=candidate_record,
        arrays=_fixture_arrays(),
    )
    write_probe_manifest(
        baseline_root,
        metadata=_probe_metadata("4.14.0.94"),
        fixtures=(baseline_entry,),
    )
    write_probe_manifest(
        candidate_root,
        metadata=_probe_metadata("5.0.0.93"),
        fixtures=(candidate_entry,),
    )

    comparison = compare_probe_roots(baseline_root, candidate_root)
    final_comparison = comparison["fixtures"][0]["final_regions"]

    assert final_comparison["angle_change_count"] == 1
    assert final_comparison["polygon_presence_change_count"] == 1
    assert comparison["final_angle_change_count"] == 1
    assert comparison["final_polygon_presence_change_count"] == 1
    assert semantic_equivalence_failures(comparison) == {
        "final_angle_change_count": 1,
        "final_polygon_presence_change_count": 1,
    }


def test_probe_comparison_gates_final_polygon_shape_changes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    baseline_root = repository / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = repository / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_record = _fixture_record()
    baseline_final = baseline_record["final_regions"][0]
    candidate_record = {
        **baseline_record,
        "final_regions": [
            {
                **baseline_final,
                "polygon": [*baseline_final["polygon"], [15, 15]],
            }
        ],
    }
    baseline_entry = write_fixture_artifact(
        baseline_root,
        fixture_file="v01/synthetic.jpg",
        record=baseline_record,
        arrays=_fixture_arrays(),
    )
    candidate_entry = write_fixture_artifact(
        candidate_root,
        fixture_file="v01/synthetic.jpg",
        record=candidate_record,
        arrays=_fixture_arrays(),
    )
    write_probe_manifest(
        baseline_root,
        metadata=_probe_metadata("4.14.0.94"),
        fixtures=(baseline_entry,),
    )
    write_probe_manifest(
        candidate_root,
        metadata=_probe_metadata("5.0.0.93"),
        fixtures=(candidate_entry,),
    )

    comparison = compare_probe_roots(baseline_root, candidate_root)

    assert comparison["final_polygon_value_change_count"] == 1
    assert semantic_equivalence_failures(comparison) == {"final_polygon_value_change_count": 1}


def test_generated_fixture_notice_preserves_terms_and_artifact_warning(tmp_path: Path) -> None:
    root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"

    notice_path = write_fixture_notice(
        root,
        source_url="https://example.test/fixture-terms",
        work="Reviewed fixture work",
        author="Fixture Author",
    )

    notice = notice_path.read_text(encoding="utf-8")
    assert "https://example.test/fixture-terms" in notice
    assert "must not be committed" in notice
    assert "source-derived" in notice


def test_opencv_ab_cli_exposes_separate_probe_and_compare_commands() -> None:
    script = Path(__file__).parents[2] / "scripts" / "ocr_opencv_ab.py"

    namespace = runpy.run_path(str(script), run_name="ocr_opencv_ab_test")
    parser = namespace["build_parser"]()
    help_text = parser.format_help()

    assert "probe" in help_text
    assert "compare" in help_text
    historical = parser.parse_args(
        [
            "compare",
            "--baseline",
            "baseline",
            "--candidate",
            "candidate",
            "--output",
            "comparison.json",
            "--allow-historical",
        ]
    )
    assert historical.allow_historical is True


def test_runtime_capture_installs_and_restores_real_vendor_boundaries() -> None:
    script = Path(__file__).parents[2] / "scripts" / "ocr_opencv_ab.py"
    namespace = runpy.run_path(str(script), run_name="ocr_opencv_ab_runtime_test")
    runtime_capture_type = namespace["_RuntimeCapture"]
    detector_module = importlib.import_module(
        "mangasensei.ocr.vendor.manga_image_translator.manga_translator.detection.default"
    )
    imgproc = importlib.import_module(
        "mangasensei.ocr.vendor.manga_image_translator.manga_translator.detection."
        "default_utils.imgproc"
    )
    recognizer_boundary = importlib.import_module("mangasensei.ocr.adapter.recognizer_48px")

    class Model:
        def infer_beam_batch_tensor(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class Recognizer:
        model = Model()

    originals = (
        imgproc.resize_aspect_ratio,
        detector_module.det_batch_forward_default,
        recognizer_boundary._RecognitionQuadrilateral.get_transformed_region,
    )
    capture = runtime_capture_type(Recognizer())

    def exercise_patched_boundaries() -> None:
        with capture.installed():
            assert imgproc.resize_aspect_ratio is not originals[0]
            assert detector_module.det_batch_forward_default is not originals[1]
            assert (
                recognizer_boundary._RecognitionQuadrilateral.get_transformed_region
                is not originals[2]
            )
            assert "infer_beam_batch_tensor" in vars(capture.recognizer.model)
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected failure"):
        exercise_patched_boundaries()

    assert imgproc.resize_aspect_ratio is originals[0]
    assert detector_module.det_batch_forward_default is originals[1]
    assert recognizer_boundary._RecognitionQuadrilateral.get_transformed_region is originals[2]
    assert "infer_beam_batch_tensor" not in vars(capture.recognizer.model)


def _fixture_record(
    *,
    detector_candidates: list[dict[str, object]] | None = None,
    crop_bbox: list[int] | None = None,
    final_text: str = "stable transcript",
) -> dict[str, object]:
    detector_candidates = detector_candidates or [{"bbox": [10, 10, 30, 50], "score": 0.9}]
    crop_bbox = crop_bbox or [10, 10, 30, 50]
    return {
        "image_sha256": "fixture-sha",
        "detector": {"candidates": detector_candidates},
        "recognizer": {
            "crops": [{"bbox": crop_bbox, "array_key": "recognizer_crop_000"}],
            "inputs": [{"bbox": crop_bbox, "array_key": "recognizer_crop_000"}],
            "accepted": [
                {
                    "bbox": crop_bbox,
                    "text": final_text,
                    "confidence": 0.9,
                }
            ],
        },
        "merge": {
            "regions": [
                {
                    "bbox": crop_bbox,
                    "text": final_text,
                    "confidence": 0.9,
                }
            ]
        },
        "final_regions": [
            {
                "bbox": crop_bbox,
                "polygon": [[crop_bbox[0], crop_bbox[1]], [crop_bbox[2], crop_bbox[3]]],
                "angle": 0.0,
                "text": final_text,
                "confidence": 0.9,
                "reading_order": 0,
            }
        ],
        "array_stages": {"detector_db": "detector_db"},
    }


def _probe_metadata(opencv_distribution: str) -> dict[str, object]:
    return {
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
    }


def _schema_two_probe_metadata(opencv_distribution: str) -> dict[str, object]:
    metadata = _probe_metadata(opencv_distribution)
    return {
        **metadata,
        "schema_version": 2,
        "runtime": {
            "python": "3.11.9",
            "python_implementation": "CPython",
            "platform": "test-platform",
            "machine": "test-machine",
            "numpy": "2.4.6",
            "torch": "2.13.0",
            "pillow": "12.0.0",
            "networkx": "3.6.1",
            "pyclipper": "1.4.0",
            "shapely": "2.1.2",
            "torchvision": "0.24.0",
            "torch_threads": 4,
            "torch_interop_threads": 4,
        },
        "opencv": {
            **cast(dict[str, object], metadata["opencv"]),
            "binary_sha256": ("4" if opencv_distribution.startswith("4") else "5") * 64,
            "opencl": False,
        },
        "model_files": [
            {
                "filename": "model.bin",
                "sha256": "a" * 64,
                "size_bytes": 1,
            }
        ],
        "source_files": {"mangasensei/ocr/adapter.py": "b" * 64},
    }


def _fixture_arrays() -> dict[str, np.ndarray]:
    return {
        "detector_db": np.asarray([[0.1, 0.2]], dtype=np.float32),
        "recognizer_crop_000": np.asarray([[10, 20]], dtype=np.uint8),
    }
