from __future__ import annotations

from pathlib import Path

import pytest

from mangasensei.ocr.diagnostics.opencv_ab import (
    compare_probe_roots,
    semantic_equivalence_failures,
)
from mangasensei.ocr.diagnostics.opencv_artifacts import (
    write_fixture_artifact,
    write_probe_manifest,
)

_FIXTURE_FILE = "v01/black_jack_v01_pdf009.jpg"
_EXPECTED_ZONES = {
    "page9_boten_edge_band": (258, 930, 272, 1220),
    "page9_boten_context": (250, 930, 285, 1220),
}


def test_required_reviewed_map_zones_fail_closed_when_both_probes_omit_them(
    tmp_path: Path,
) -> None:
    baseline_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-5"
    _write_probe(baseline_root, "4.14.0.94", {})
    _write_probe(candidate_root, "5.0.0.93", {})

    with pytest.raises(ValueError, match="missing reviewed map-zone evidence"):
        compare_probe_roots(
            baseline_root,
            candidate_root,
            expected_reviewed_map_zones={_FIXTURE_FILE: _EXPECTED_ZONES},
        )


def test_reviewed_map_zone_drift_fails_the_semantic_gate(tmp_path: Path) -> None:
    baseline_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_zones = {
        name: _zone_evidence(source_zone, mean=index / 10)
        for index, (name, source_zone) in enumerate(_EXPECTED_ZONES.items(), start=1)
    }
    candidate_zones = {
        **baseline_zones,
        "page9_boten_context": {
            **baseline_zones["page9_boten_context"],
            "mean": 0.21,
        },
    }
    _write_probe(baseline_root, "4.14.0.94", baseline_zones)
    _write_probe(candidate_root, "5.0.0.93", candidate_zones)

    comparison = compare_probe_roots(
        baseline_root,
        candidate_root,
        expected_reviewed_map_zones={_FIXTURE_FILE: _EXPECTED_ZONES},
    )

    assert comparison["reviewed_map_zone_change_count"] == 1
    assert semantic_equivalence_failures(comparison) == {"reviewed_map_zone_change_count": 1}


def test_reviewed_map_zones_reject_wrong_source_coordinates(tmp_path: Path) -> None:
    baseline_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-5"
    wrong_zones = {name: _zone_evidence((0, 0, 1, 1), mean=0.1) for name in _EXPECTED_ZONES}
    _write_probe(baseline_root, "4.14.0.94", wrong_zones)
    _write_probe(candidate_root, "5.0.0.93", wrong_zones)

    with pytest.raises(ValueError, match="map-zone evidence is malformed"):
        compare_probe_roots(
            baseline_root,
            candidate_root,
            expected_reviewed_map_zones={_FIXTURE_FILE: _EXPECTED_ZONES},
        )


def _write_probe(
    root: Path,
    opencv_distribution: str,
    zones: dict[str, dict[str, object]],
) -> None:
    fixture = write_fixture_artifact(
        root,
        fixture_file=_FIXTURE_FILE,
        record={
            "image_sha256": "fixture-sha",
            "detector": {"candidates": [], "reviewed_map_zones": zones},
            "recognizer": {"crops": [], "inputs": [], "accepted": []},
            "merge": {"regions": []},
            "final_regions": [],
            "array_stages": {},
        },
        arrays={},
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


def _zone_evidence(source_zone: tuple[int, int, int, int], *, mean: float) -> dict[str, object]:
    return {
        "source_zone": list(source_zone),
        "map_bounds": [1, 2, 3, 4],
        "value_count": 10,
        "minimum": 0.0,
        "maximum": 1.0,
        "mean": mean,
        "percentile_05": 0.05,
        "percentile_50": 0.5,
        "percentile_95": 0.95,
    }
