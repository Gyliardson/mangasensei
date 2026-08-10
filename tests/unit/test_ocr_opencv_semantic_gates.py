from __future__ import annotations

import json
import runpy
import sys
from collections.abc import Callable
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


@pytest.mark.parametrize(
    ("mutate", "expected_failure"),
    [
        (
            lambda record: {
                **record,
                "recognizer": {
                    **record["recognizer"],
                    "accepted": [
                        {
                            **record["recognizer"]["accepted"][0],
                            "text": "changed recognizer transcript",
                        }
                    ],
                },
            },
            {"recognizer_text_change_count": 1},
        ),
        (
            lambda record: {
                **record,
                "merge": {
                    "regions": [
                        {
                            **record["merge"]["regions"][0],
                            "text": "changed merge transcript",
                        }
                    ]
                },
            },
            {"merge_text_change_count": 1},
        ),
        (
            lambda record: {
                **record,
                "final_regions": [
                    {
                        **record["final_regions"][0],
                        "text": "changed final transcript",
                    }
                ],
            },
            {"semantic_text_change_count": 1},
        ),
        (
            lambda record: {
                **record,
                "merge": {"regions": [{**record["merge"]["regions"][0], "angle": 1.0}]},
            },
            {"merge_angle_change_count": 1},
        ),
        (
            lambda record: {
                **record,
                "final_regions": [{**record["final_regions"][0], "id": "changed"}],
            },
            {"final_id_change_count": 1},
        ),
        (
            lambda record: {
                **record,
                "final_regions": [{**record["final_regions"][0], "reading_order": 1}],
            },
            {"final_reading_order_change_count": 1},
        ),
        (
            lambda record: {**record, "final_regions": []},
            {"unmatched_final_regions": 1},
        ),
    ],
)
def test_semantic_mutations_reach_the_top_level_gate(
    tmp_path: Path,
    mutate: Callable[[dict[str, object]], dict[str, object]],
    expected_failure: dict[str, int],
) -> None:
    baseline_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_record = _record()
    _write_probe(baseline_root, "4.14.0.94", baseline_record)
    _write_probe(candidate_root, "5.0.0.93", mutate(baseline_record))

    comparison = compare_probe_roots(baseline_root, candidate_root)

    assert semantic_equivalence_failures(comparison) == expected_failure
    serialized = json.dumps(comparison)
    assert "controlled transcript" not in serialized
    assert "changed recognizer transcript" not in serialized
    assert "changed merge transcript" not in serialized
    assert "changed final transcript" not in serialized
    assert '"baseline_text":' not in serialized
    assert '"candidate_text":' not in serialized
    if any("text_change_count" in key for key in expected_failure):
        changes = [
            *comparison["recognizer_text_changes"],
            *comparison["merge_text_changes"],
            *comparison["text_changes"],
        ]
        assert len(changes) == 1
        for key in ("baseline_text_sha256", "candidate_text_sha256"):
            assert len(changes[0][key]) == 64
            assert set(changes[0][key]) <= set("0123456789abcdef")


def test_compare_cli_exits_nonzero_without_printing_changed_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-4"
    candidate_root = tmp_path / "var" / "ocr-opencv-ab" / "opencv-5"
    baseline_record = _record()
    candidate_record = {
        **baseline_record,
        "final_regions": [
            {
                **baseline_record["final_regions"][0],
                "text": "changed final transcript",
            }
        ],
    }
    _write_probe(baseline_root, "4.14.0.94", baseline_record)
    _write_probe(candidate_root, "5.0.0.93", candidate_record)
    comparison = compare_probe_roots(baseline_root, candidate_root)
    script = Path(__file__).parents[2] / "scripts" / "ocr_opencv_ab.py"
    namespace = runpy.run_path(str(script), run_name="ocr_opencv_ab_cli_gate_test")
    main = namespace["main"]
    monkeypatch.setitem(
        main.__globals__,
        "compare_and_write",
        lambda *_args, **_kwargs: comparison,
    )
    monkeypatch.setitem(
        main.__globals__,
        "validate_artifact_root",
        lambda path, **_kwargs: Path(path),
    )
    monkeypatch.setitem(main.__globals__, "_file_sha256", lambda _path: "a" * 64)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(script),
            "compare",
            "--baseline",
            "baseline",
            "--candidate",
            "candidate",
            "--output",
            "comparison.json",
            "--allow-historical",
        ],
    )

    with pytest.raises(SystemExit, match="OCR_OPENCV_REGRESSION"):
        main()

    output = capsys.readouterr().out
    assert "changed final transcript" not in output
    assert "semantic_text_change_count=1" in output


def _record() -> dict[str, object]:
    region = {
        "bbox": [10, 10, 30, 50],
        "polygon": [[10, 10], [30, 50]],
        "angle": 0.0,
        "text": "controlled transcript",
        "confidence": 0.9,
        "reading_order": 0,
        "id": "stable",
    }
    return {
        "image_sha256": "fixture-sha",
        "detector": {"candidates": []},
        "recognizer": {"crops": [], "inputs": [], "accepted": [region]},
        "merge": {"regions": [region]},
        "final_regions": [region],
        "array_stages": {},
    }


def _write_probe(root: Path, opencv_distribution: str, record: dict[str, object]) -> None:
    fixture = write_fixture_artifact(
        root,
        fixture_file="v01/synthetic.jpg",
        record=record,
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
