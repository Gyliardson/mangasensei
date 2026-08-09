from __future__ import annotations

import runpy
from pathlib import Path

import numpy as np
import pytest

from mangasensei.ocr.diagnostics.opencv_ab import (
    array_delta,
    array_descriptor,
    array_fingerprint,
    compare_probe_roots,
    safe_comparison_summary,
    source_zone_statistics,
    spatially_match,
    validate_artifact_root,
    write_fixture_artifact,
    write_fixture_notice,
    write_probe_manifest,
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
    probability_map = np.arange(64 * 32, dtype=np.float32).reshape(1, 1, 64, 32)

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
    assert comparison["semantic_text_change_count"] == 0
    assert comparison["unmatched_detector_candidates"] == 0
    assert comparison["unmatched_final_regions"] == 0
    fixture_comparison = comparison["fixtures"][0]
    assert fixture_comparison["detector"]["matched_count"] == 2
    assert fixture_comparison["arrays"]["detector_db"]["changed_values"] == 1
    assert fixture_comparison["recognizer_crops"][0]["delta"]["changed_values"] == 1


def test_probe_comparison_rejects_different_source_or_model_inputs(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    baseline_root = repository / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = repository / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_entry = write_fixture_artifact(
        baseline_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays={},
    )
    candidate_entry = write_fixture_artifact(
        candidate_root,
        fixture_file="v01/synthetic.jpg",
        record=_fixture_record(),
        arrays={},
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
            "crops": [{"bbox": crop_bbox, "array_key": "recognizer_crop_000"}]
        },
        "final_regions": [
            {
                "bbox": crop_bbox,
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
        },
        "fixture_manifest_sha256": "fixture-manifest-sha",
        "model_manifest_sha256": "model-manifest-sha",
        "ocr_config_digest": "ocr-config-digest",
    }
