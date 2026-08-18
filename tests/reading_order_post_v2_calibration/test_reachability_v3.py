from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import scripts.reading_order_post_v2_qualification.run_arm as run_arm_module
from PIL import Image
from scripts.reading_order_post_v2_qualification import INPUT_SCHEMA_VERSION
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import build_exercise_report_v3

import mangasensei.ocr.diagnostics.reading_order_post_v2_calibration as candidate_module
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation

PAGE_ID = "Q901"
EXECUTION_SHA = "0" * 40


def _region_id(index: int) -> str:
    return f"reachability-r{index}"


def _write_corpus(
    root: Path,
    region_boxes: tuple[tuple[int, int, int, int], ...],
) -> Path:
    corpus = root / "corpus"
    (corpus / "inputs").mkdir(parents=True)
    (corpus / "images").mkdir(parents=True)
    regions: list[dict[str, object]] = []
    for index, (x1, y1, x2, y2) in enumerate(region_boxes):
        regions.append(
            {
                "regionId": _region_id(index),
                "sourceIndex": index,
                "lines": [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]],
                "angle": 0,
            }
        )
    (corpus / "inputs" / f"{PAGE_ID}.json").write_text(
        json.dumps(
            {
                "schemaVersion": INPUT_SCHEMA_VERSION,
                "pageId": PAGE_ID,
                "width": 1000,
                "height": 1000,
                "regions": regions,
            }
        ),
        encoding="utf-8",
    )
    Image.new("RGB", (1000, 1000), "white").save(corpus / "images" / f"{PAGE_ID}.png")
    return corpus


def _patch_panels(
    monkeypatch: pytest.MonkeyPatch,
    segmentation: PanelSegmentation,
) -> None:
    monkeypatch.setattr(candidate_module, "segment_panel_groups", lambda _pixels: segmentation)
    monkeypatch.setattr(run_arm_module, "segment_panel_groups", lambda _pixels: segmentation)


def _execute(
    *,
    corpus: Path,
    output_root: Path,
    arm: ArmId,
    repeat: int,
) -> dict[str, object]:
    diagnostic_path, _ = run_arm_module.execute_page(
        corpus_root=corpus,
        page_id=PAGE_ID,
        arm_id=arm,
        execution_sha=EXECUTION_SHA,
        repeat=repeat,
        output_root=output_root,
    )
    loaded = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _page(
    slice_name: str,
    region_count: int,
    *,
    pair: tuple[int, int],
) -> PageGroundTruth:
    ids = tuple(_region_id(index) for index in range(region_count))
    earlier_index, later_index = pair
    return PageGroundTruth(
        page_id=PAGE_ID,
        reading_order=ids,
        unscored_region_ids=(),
        qualification_pairs=(
            QualificationPair(
                "p1",
                ids[earlier_index],
                ids[later_index],
                (slice_name,),
            ),
        ),
        layout_tags=(),
    )


def _diagnostics_for_arms(
    *,
    corpus: Path,
    output_root: Path,
    arms: tuple[ArmId, ...],
) -> dict[ArmId, dict[str, dict[str, object]]]:
    return {
        arm: {PAGE_ID: _execute(corpus=corpus, output_root=output_root, arm=arm, repeat=1)}
        for arm in arms
    }


def _assert_composed_count_and_repeat(
    *,
    metric: str,
    page: PageGroundTruth,
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
    repeat_arm: ArmId,
    corpus: Path,
    output_root: Path,
) -> dict[str, object]:
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts[metric].count == 1
    first = diagnostics[repeat_arm][PAGE_ID]
    second = _execute(corpus=corpus, output_root=output_root, arm=repeat_arm, repeat=2)
    assert first == second
    return first


def _complete_frame_lines(box: PanelBox) -> tuple[tuple[float, float, float, float], ...]:
    return (
        (float(box.x1), float(box.y1), float(box.x2), float(box.y1)),
        (float(box.x1), float(box.y2), float(box.x2), float(box.y2)),
        (float(box.x1), float(box.y1), float(box.x1), float(box.y2)),
        (float(box.x2), float(box.y1), float(box.x2), float(box.y2)),
    )


def test_v3_reachability_c1_candidate_to_production_diagnostic_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    segmentation = PanelSegmentation(
        (PanelBox(10, 0, 110, 100), PanelBox(200, 0, 300, 100)),
        True,
        "reliable",
    )
    _patch_panels(monkeypatch, segmentation)
    corpus = _write_corpus(
        tmp_path,
        ((0, 40, 20, 60), (30, 20, 50, 40), (220, 20, 240, 40)),
    )
    output_root = tmp_path / "out"
    diagnostics = _diagnostics_for_arms(
        corpus=corpus,
        output_root=output_root,
        arms=(ArmId.CONTROL, ArmId.C1_ONLY),
    )
    page = _page("c1-boundary-positive", 3, pair=(0, 1))
    diagnostic = _assert_composed_count_and_repeat(
        metric="c1_guarded_pairs",
        page=page,
        diagnostics=diagnostics,
        repeat_arm=ArmId.C1_ONLY,
        corpus=corpus,
        output_root=output_root,
    )
    before = diagnostics[ArmId.CONTROL][PAGE_ID]["assignments"]
    after = diagnostic["assignments"]
    assert isinstance(before, list)
    assert isinstance(after, list)
    assert before[0]["status"] == "confident"
    assert after[0]["status"] == "unassigned"


@pytest.mark.parametrize(
    ("metric", "slice_name", "panels", "regions", "pair", "expected_rule"),
    [
        (
            "c2_gutter_pairs",
            "c2-gutter-bridge",
            (PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100)),
            ((20, 20, 40, 40), (220, 20, 240, 40), (140, 40, 160, 60)),
            (0, 2),
            "unique-gutter-between-hard-panels",
        ),
        (
            "c2_overlap_pairs",
            "c2-ambiguous-overlap-bridge",
            (PanelBox(0, 0, 120, 100), PanelBox(100, 0, 220, 100)),
            ((20, 20, 40, 40), (180, 20, 200, 40), (105, 40, 115, 60)),
            (0, 2),
            "validated-overlap-bridge-right-before-left",
        ),
        (
            "c2_pair_precedence_pairs",
            "c2-pair-precedence-slot",
            (
                PanelBox(0, 0, 100, 100),
                PanelBox(0, 200, 100, 300),
                PanelBox(0, 400, 100, 500),
            ),
            (
                (20, 20, 40, 40),
                (20, 220, 40, 240),
                (20, 420, 40, 440),
                (20, 110, 40, 190),
            ),
            (0, 3),
            "uncertain-",
        ),
    ],
)
def test_v3_reachability_c2_rule_candidate_to_production_diagnostic_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metric: str,
    slice_name: str,
    panels: tuple[PanelBox, ...],
    regions: tuple[tuple[int, int, int, int], ...],
    pair: tuple[int, int],
    expected_rule: str,
) -> None:
    _patch_panels(monkeypatch, PanelSegmentation(panels, True, "reliable"))
    corpus = _write_corpus(tmp_path, regions)
    output_root = tmp_path / "out"
    diagnostics = _diagnostics_for_arms(
        corpus=corpus,
        output_root=output_root,
        arms=(ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1),
    )
    page = _page(slice_name, len(regions), pair=pair)
    diagnostic = _assert_composed_count_and_repeat(
        metric=metric,
        page=page,
        diagnostics=diagnostics,
        repeat_arm=ArmId.C2_ONLY,
        corpus=corpus,
        output_root=output_root,
    )
    edges = diagnostic["relationEdges"]
    assert isinstance(edges, list)
    rules = tuple(str(edge["rule"]) for edge in edges)
    if expected_rule.endswith("-"):
        assert any(rule.startswith(expected_rule) for rule in rules)
    else:
        assert expected_rule in rules


def test_v3_reachability_c2_fail_closed_candidate_to_production_diagnostic_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300))
    regions = ((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450))
    _patch_panels(monkeypatch, PanelSegmentation(panels, True, "reliable"))
    corpus = _write_corpus(tmp_path, regions)
    output_root = tmp_path / "out"
    diagnostics = _diagnostics_for_arms(
        corpus=corpus,
        output_root=output_root,
        arms=(ArmId.C2_ONLY,),
    )
    page = _page("c2-one-sided-non-unique-fail-closed", 3, pair=(0, 2))
    diagnostic = _assert_composed_count_and_repeat(
        metric="c2_fail_closed_no_relation_pairs",
        page=page,
        diagnostics=diagnostics,
        repeat_arm=ArmId.C2_ONLY,
        corpus=corpus,
        output_root=output_root,
    )
    assert diagnostic["relationEdges"] == []
    assert diagnostic["fallbackReason"] is None


def test_v3_reachability_c2_conflict_candidate_to_production_diagnostic_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(300, 200, 400, 300))
    regions = ((20, 20, 40, 40), (320, 220, 340, 240), (150, 50, 250, 250))
    _patch_panels(monkeypatch, PanelSegmentation(panels, True, "reliable"))
    corpus = _write_corpus(tmp_path, regions)
    output_root = tmp_path / "out"
    diagnostics = _diagnostics_for_arms(
        corpus=corpus,
        output_root=output_root,
        arms=(ArmId.CONTROL, ArmId.C2_ONLY),
    )
    page = _page("c2-conflict-cycle-safety", 3, pair=(0, 1))
    diagnostic = _assert_composed_count_and_repeat(
        metric="c2_conflict_cycle_fallback_pairs",
        page=page,
        diagnostics=diagnostics,
        repeat_arm=ArmId.C2_ONLY,
        corpus=corpus,
        output_root=output_root,
    )
    assert diagnostic["fallbackReason"] == "uncertain-relation-conflict"
    assert diagnostic["usedPanelEvidence"] is False
    assert diagnostic["finalOrder"] == diagnostic["fallbackOrder"]


@pytest.mark.parametrize(
    ("metric", "slice_name", "lines", "accepted"),
    [
        (
            "c3_positive_pairs",
            "c3-positive-recovery",
            _complete_frame_lines(PanelBox(400, 300, 700, 700))
            + (
                (100.0, 100.0, 500.0, 100.0),
                (100.0, 500.0, 500.0, 500.0),
                (100.0, 100.0, 100.0, 500.0),
                (500.0, 100.0, 500.0, 300.0),
            ),
            True,
        ),
        (
            "c3_rejection_pages",
            "c3-invalid-topology-negative",
            (
                (200.0, 200.0, 500.0, 200.0),
                (200.0, 800.0, 500.0, 800.0),
                (200.0, 200.0, 200.0, 500.0),
                (800.0, 200.0, 800.0, 500.0),
            ),
            False,
        ),
    ],
)
def test_v3_reachability_c3_candidate_to_production_diagnostic_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metric: str,
    slice_name: str,
    lines: tuple[tuple[float, float, float, float], ...],
    accepted: bool,
) -> None:
    merged = PanelBox(100, 100, 900, 900)
    segmentation = PanelSegmentation((merged,), False, "fewer-than-two-groups")
    _patch_panels(monkeypatch, segmentation)
    monkeypatch.setattr(candidate_module, "_line_segments", lambda _gray: lines)
    regions = ((150, 150, 200, 250), (600, 500, 650, 600))
    corpus = _write_corpus(tmp_path, regions)
    output_root = tmp_path / "out"
    diagnostics = _diagnostics_for_arms(
        corpus=corpus,
        output_root=output_root,
        arms=(ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1),
    )
    page = _page(slice_name, 2, pair=(0, 1))
    diagnostic = _assert_composed_count_and_repeat(
        metric=metric,
        page=page,
        diagnostics=diagnostics,
        repeat_arm=ArmId.C3_ONLY,
        corpus=corpus,
        output_root=output_root,
    )
    if accepted:
        assert diagnostic["segmentation"]["reason"] == "recovered-merged-frame"
        assert (
            diagnostic["recoveryReason"]
            == "accepted-strong-anchor-plus-occlusion-supported-frame"
        )
    else:
        assert str(diagnostic["recoveryReason"]).startswith("rejected-")
        assert diagnostic["usedPanelEvidence"] is False
        assert diagnostic["finalOrder"] == diagnostic["fallbackOrder"]


@pytest.mark.parametrize(
    ("metric", "slice_name", "regions", "expected_directions"),
    [
        (
            "b1_horizontal_pairs",
            "b1-horizontal",
            ((220, 20, 260, 40), (250, 50, 290, 70), (20, 20, 40, 60)),
            ("h", "h"),
        ),
        (
            "b1_vertical_pairs",
            "b1-vertical",
            ((220, 20, 240, 60), (250, 20, 270, 60), (20, 20, 60, 40)),
            ("v", "v"),
        ),
        (
            "b1_mixed_pairs",
            "b1-mixed-orientation",
            ((220, 20, 260, 40), (250, 20, 270, 60), (20, 20, 60, 40)),
            ("h", "v"),
        ),
    ],
)
def test_v3_reachability_b1_candidate_to_production_diagnostic_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    metric: str,
    slice_name: str,
    regions: tuple[tuple[int, int, int, int], ...],
    expected_directions: tuple[str, str],
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(200, 0, 320, 120))
    _patch_panels(monkeypatch, PanelSegmentation(panels, True, "reliable"))
    corpus = _write_corpus(tmp_path, regions)
    output_root = tmp_path / "out"
    diagnostics = _diagnostics_for_arms(
        corpus=corpus,
        output_root=output_root,
        arms=(ArmId.B1_ONLY, ArmId.C1_C2_C3_B1),
    )
    page = _page(slice_name, 3, pair=(0, 1))
    diagnostic = _assert_composed_count_and_repeat(
        metric=metric,
        page=page,
        diagnostics=diagnostics,
        repeat_arm=ArmId.B1_ONLY,
        corpus=corpus,
        output_root=output_root,
    )
    directions = diagnostic["regionDirections"]
    assert isinstance(directions, dict)
    assert (directions[_region_id(0)], directions[_region_id(1)]) == expected_directions


def test_v3_reachability_monkeypatches_only_frozen_upstream_seams() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    patched_names = {
        call.args[1].value
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "setattr"
        and len(call.args) >= 2
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
    }
    forbidden = {
        "_assignment_observations",
        "_uncertain_relation_edges",
        "_recover_merged_frames",
        "_b1_local_order",
    }
    allowed = {"segment_panel_groups", "_line_segments"}
    assert forbidden.isdisjoint(patched_names)
    assert patched_names <= allowed
