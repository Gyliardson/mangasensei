from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mangasensei.ocr.diagnostics.opencv_ab import (
    array_delta,
    array_fingerprint,
    safe_comparison_summary,
    spatially_match,
    validate_artifact_root,
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
