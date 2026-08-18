from __future__ import annotations

import numpy as np
import pytest

import mangasensei.ocr.diagnostics.reading_order_post_v2_calibration as candidate_module
from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    CalibrationConfig,
    CalibrationResult,
    run_post_v2_calibration_candidate,
)
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ExperimentRegion
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation


class _SyntheticRegion:
    def __init__(
        self,
        xyxy: tuple[int, int, int, int],
        *,
        direction: str = "v",
    ) -> None:
        self.xyxy = xyxy
        self.direction = direction


def _refs(
    region_boxes: tuple[tuple[int, int, int, int], ...],
    *,
    directions: tuple[str, ...] | None = None,
) -> tuple[ExperimentRegion, ...]:
    if directions is None:
        directions = tuple("v" for _ in region_boxes)
    assert len(directions) == len(region_boxes)
    return tuple(
        ExperimentRegion(
            f"reachability-r{index}",
            index,
            _SyntheticRegion(box, direction=directions[index]),
        )
        for index, box in enumerate(region_boxes)
    )


def _run_with_panels(
    monkeypatch: pytest.MonkeyPatch,
    *,
    panel_boxes: tuple[PanelBox, ...],
    region_boxes: tuple[tuple[int, int, int, int], ...],
    config: CalibrationConfig,
    directions: tuple[str, ...] | None = None,
) -> CalibrationResult:
    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    refs = _refs(region_boxes, directions=directions)
    monkeypatch.setattr(
        candidate_module,
        "segment_panel_groups",
        lambda _pixels: PanelSegmentation(panel_boxes, True, "reliable"),
    )
    return run_post_v2_calibration_candidate(
        pixels,
        refs,
        page_height=1000,
        config=config,
    )


def _complete_frame_lines(box: PanelBox) -> tuple[tuple[float, float, float, float], ...]:
    return (
        (float(box.x1), float(box.y1), float(box.x2), float(box.y1)),
        (float(box.x1), float(box.y2), float(box.x2), float(box.y2)),
        (float(box.x1), float(box.y1), float(box.x1), float(box.y2)),
        (float(box.x2), float(box.y1), float(box.x2), float(box.y2)),
    )


def _run_c3(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lines: tuple[tuple[float, float, float, float], ...],
) -> CalibrationResult:
    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    refs = _refs(((760, 150, 800, 230), (180, 740, 230, 820)))
    merged = PanelBox(100, 100, 900, 900)
    monkeypatch.setattr(
        candidate_module,
        "segment_panel_groups",
        lambda _pixels: PanelSegmentation((merged,), False, "fewer-than-two-groups"),
    )
    monkeypatch.setattr(candidate_module, "_line_segments", lambda _gray: lines)
    return run_post_v2_calibration_candidate(
        pixels,
        refs,
        page_height=1000,
        config=CalibrationConfig(c3_merged_frame_recovery=True),
    )


def _rules(result: CalibrationResult) -> tuple[str, ...]:
    return tuple(edge.rule for edge in result.diagnostic.relation_edges)


def test_v3_reachability_c1_guard_is_synthetic_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100))
    regions = ((-10, 40, 10, 60), (20, 20, 40, 40), (220, 20, 240, 40))
    control = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        config=CalibrationConfig(),
    )
    guarded = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        config=CalibrationConfig(c1_boundary_guard=True),
    )
    repeat = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        config=CalibrationConfig(c1_boundary_guard=True),
    )
    before = control.diagnostic.assignments[0]
    after = guarded.diagnostic.assignments[0]
    assert before.status == "confident"
    assert before.candidate_group_indices == (0,)
    assert after.status == "unassigned"
    assert after.candidate_group_indices == ()
    assert guarded.diagnostic == repeat.diagnostic


def test_v3_reachability_c2_gutter_rule_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_panels(
        monkeypatch,
        panel_boxes=(PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100)),
        region_boxes=((20, 20, 40, 40), (220, 20, 240, 40), (140, 40, 160, 60)),
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert result.diagnostic.assignments[2].status == "unassigned"
    assert "unique-gutter-between-hard-panels" in _rules(result)


def test_v3_reachability_c2_overlap_rule_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_panels(
        monkeypatch,
        panel_boxes=(PanelBox(0, 0, 120, 100), PanelBox(100, 0, 220, 100)),
        region_boxes=((20, 20, 40, 40), (180, 20, 200, 40), (105, 40, 115, 60)),
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert result.diagnostic.assignments[2].status == "ambiguous"
    assert "validated-overlap-bridge-right-before-left" in _rules(result)


def test_v3_reachability_c2_pair_precedence_rule_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_panels(
        monkeypatch,
        panel_boxes=(
            PanelBox(0, 0, 100, 100),
            PanelBox(0, 200, 100, 300),
            PanelBox(0, 400, 100, 500),
        ),
        region_boxes=(
            (20, 20, 40, 40),
            (20, 220, 40, 240),
            (20, 420, 40, 440),
            (20, 110, 40, 190),
        ),
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert result.diagnostic.assignments[3].status == "unassigned"
    assert any(rule.startswith("uncertain-") for rule in _rules(result))


def test_v3_reachability_c2_fail_closed_no_relation_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_panels(
        monkeypatch,
        panel_boxes=(PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300)),
        region_boxes=((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450)),
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert result.diagnostic.assignments[2].status == "unassigned"
    assert result.diagnostic.relation_edges == ()
    assert result.diagnostic.fallback_reason is None


def test_v3_reachability_c2_conflict_fallback_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run_with_panels(
        monkeypatch,
        panel_boxes=(PanelBox(0, 0, 100, 100), PanelBox(300, 200, 400, 300)),
        region_boxes=((20, 20, 40, 40), (320, 220, 340, 240), (150, 50, 250, 250)),
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert result.diagnostic.fallback_reason == "uncertain-relation-conflict"
    assert not result.diagnostic.used_panel_evidence
    assert result.diagnostic.final_order == result.diagnostic.fallback_order


def test_v3_reachability_c3_positive_recovery_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = PanelBox(400, 300, 700, 700)
    lines = _complete_frame_lines(anchor) + (
        (100.0, 100.0, 500.0, 100.0),
        (100.0, 500.0, 500.0, 500.0),
        (100.0, 100.0, 100.0, 500.0),
        (500.0, 100.0, 500.0, 300.0),
    )
    first = _run_c3(monkeypatch, lines=lines)
    second = _run_c3(monkeypatch, lines=lines)
    assert first.diagnostic.segmentation_reliable
    assert first.diagnostic.segmentation_reason == "recovered-merged-frame"
    assert (
        first.diagnostic.recovery_reason
        == "accepted-strong-anchor-plus-occlusion-supported-frame"
    )
    assert first.diagnostic == second.diagnostic


def test_v3_reachability_c3_generic_rejection_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = (
        (200.0, 200.0, 500.0, 200.0),
        (200.0, 800.0, 500.0, 800.0),
        (200.0, 200.0, 200.0, 500.0),
        (800.0, 200.0, 800.0, 500.0),
    )
    first = _run_c3(monkeypatch, lines=lines)
    second = _run_c3(monkeypatch, lines=lines)
    assert first.diagnostic.recovery_reason.startswith("rejected-")
    assert not first.diagnostic.used_panel_evidence
    assert first.diagnostic.final_order == first.diagnostic.fallback_order
    assert first.diagnostic == second.diagnostic


@pytest.mark.parametrize(
    ("directions", "mode"),
    [
        (("h", "h", "v"), "horizontal"),
        (("v", "v", "h"), "vertical"),
        (("h", "v", "h"), "mixed"),
    ],
)
def test_v3_reachability_b1_same_panel_orientation_is_synthetic(
    monkeypatch: pytest.MonkeyPatch,
    directions: tuple[str, ...],
    mode: str,
) -> None:
    result = _run_with_panels(
        monkeypatch,
        panel_boxes=(PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100)),
        region_boxes=((220, 20, 240, 40), (250, 20, 270, 40), (20, 20, 40, 40)),
        directions=directions,
        config=CalibrationConfig(b1_local_order=True),
    )
    first, second = result.diagnostic.assignments[:2]
    assert result.diagnostic.used_panel_evidence
    assert first.status == second.status == "confident"
    assert first.assigned_group_index == second.assigned_group_index == 1
    observed = {directions[0], directions[1]}
    if mode == "horizontal":
        assert observed == {"h"}
    elif mode == "vertical":
        assert observed == {"v"}
    else:
        assert observed == {"h", "v"}


def test_v3_reachability_cases_do_not_patch_candidate_mechanism_functions() -> None:
    forbidden = {
        "_assignment_observations",
        "_uncertain_relation_edges",
        "_recover_merged_frames",
        "_b1_local_order",
    }
    patched_upstream_seams = {"segment_panel_groups", "_line_segments"}
    assert forbidden.isdisjoint(patched_upstream_seams)
