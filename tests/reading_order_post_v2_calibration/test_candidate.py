from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from scripts.reading_order_v2.contracts import PAGE_IDS, arm_asset_paths, load_ground_truth
from scripts.reading_order_v2.fixtures import load_textblock_regions
from scripts.reading_order_v2.validate_corpus import CORPUS_ROOT

import mangasensei.ocr.diagnostics.reading_order_post_v2_calibration as candidate_module
from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    CalibrationConfig,
    CalibrationResult,
    run_post_v2_calibration_candidate,
)
from mangasensei.ocr.diagnostics.reading_order_v2 import run_reading_order_v2_arm
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId, ExperimentRegion
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation

REPOSITORY_SHA = "5" * 40
FULL_PANEL_FIX = CalibrationConfig(
    c1_boundary_guard=True,
    c2_uncertain_relations=True,
    c3_merged_frame_recovery=True,
)
DEFAULT_MERGED_FRAME = PanelBox(100, 100, 900, 900)


class _SyntheticRegion:
    def __init__(self, xyxy: tuple[int, int, int, int]) -> None:
        self.xyxy = xyxy
        self.direction = "v"


def _load_case(page_id: str) -> tuple[Any, tuple[ExperimentRegion, ...], np.ndarray]:
    image_path, input_path = arm_asset_paths(CORPUS_ROOT, page_id)
    page, regions = load_textblock_regions(input_path)
    with Image.open(image_path) as opened:
        pixels = np.asarray(opened.convert("RGB"))
    return page, regions, pixels


def _run(page_id: str, config: CalibrationConfig) -> CalibrationResult:
    page, regions, pixels = _load_case(page_id)
    return run_post_v2_calibration_candidate(
        pixels,
        regions,
        page_height=page.height,
        config=config,
    )


def _order(result: CalibrationResult) -> tuple[str, ...]:
    return tuple(item.region_id for item in result.ordered_regions)


def _ground_truth_order(page_id: str) -> tuple[str, ...]:
    annotation = CORPUS_ROOT / "annotations" / f"{page_id}.json"
    return load_ground_truth(annotation).reading_order


def _synthetic_refs(
    *region_boxes: tuple[int, int, int, int],
) -> tuple[ExperimentRegion, ...]:
    return tuple(
        ExperimentRegion(f"synthetic-r{index}", index, _SyntheticRegion(box))
        for index, box in enumerate(region_boxes)
    )


def _run_synthetic_panels(
    monkeypatch: pytest.MonkeyPatch,
    *,
    panel_boxes: tuple[PanelBox, ...],
    region_boxes: tuple[tuple[int, int, int, int], ...],
    config: CalibrationConfig,
) -> tuple[tuple[ExperimentRegion, ...], CalibrationResult]:
    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    refs = _synthetic_refs(*region_boxes)
    monkeypatch.setattr(
        candidate_module,
        "segment_panel_groups",
        lambda _pixels: PanelSegmentation(panel_boxes, True, "reliable"),
    )
    return refs, run_post_v2_calibration_candidate(
        pixels,
        refs,
        page_height=1000,
        config=config,
    )


def _relation_status(
    *,
    panel_boxes: tuple[PanelBox, ...],
    refs: tuple[ExperimentRegion, ...],
    groups: tuple[tuple[int, ...], ...],
    region_index: int,
) -> tuple[tuple[tuple[int, int, str], ...], str]:
    raw_regions = tuple(ref.region for ref in refs)
    assignments = candidate_module._assignment_observations(
        panel_boxes,
        refs,
        guarded=False,
    )
    panel_order = candidate_module._hard_panel_order(
        panel_boxes,
        tuple(range(len(panel_boxes))),
    )
    assert panel_order is not None
    hard_edges = candidate_module._panel_precedence_edges(panel_boxes)
    active_panel_order = tuple(index for index in panel_order if groups[index])
    return candidate_module._uncertain_relation_edges(
        region_index=region_index,
        assignment=assignments[region_index],
        raw_regions=raw_regions,
        boxes=panel_boxes,
        groups=groups,
        hard_edges=hard_edges,
        active_panel_order=active_panel_order,
        uncertain_node=len(panel_boxes),
    )


def _complete_frame_lines(box: PanelBox) -> tuple[tuple[float, float, float, float], ...]:
    return (
        (float(box.x1), float(box.y1), float(box.x2), float(box.y1)),
        (float(box.x1), float(box.y2), float(box.x2), float(box.y2)),
        (float(box.x1), float(box.y1), float(box.x1), float(box.y2)),
        (float(box.x2), float(box.y1), float(box.x2), float(box.y2)),
    )


def _run_synthetic_c3(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lines: tuple[tuple[float, float, float, float], ...],
    merged: PanelBox = DEFAULT_MERGED_FRAME,
) -> tuple[tuple[ExperimentRegion, ...], CalibrationResult]:
    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    refs = _synthetic_refs(
        (760, 150, 800, 230),
        (180, 740, 230, 820),
    )
    monkeypatch.setattr(
        candidate_module,
        "segment_panel_groups",
        lambda _pixels: PanelSegmentation((merged,), False, "fewer-than-two-groups"),
    )
    monkeypatch.setattr(candidate_module, "_line_segments", lambda _gray: lines)
    return refs, run_post_v2_calibration_candidate(
        pixels,
        refs,
        page_height=1000,
        config=CalibrationConfig(c3_merged_frame_recovery=True),
    )


def _assert_exact_synthetic_fallback(
    refs: tuple[ExperimentRegion, ...],
    result: CalibrationResult,
) -> None:
    assert not result.diagnostic.segmentation_reliable
    assert result.diagnostic.assignments == ()
    assert result.diagnostic.relation_edges == ()
    assert result.diagnostic.node_order == ()
    assert not result.diagnostic.used_panel_evidence
    assert _order(result) == result.diagnostic.fallback_order
    assert result.diagnostic.final_order == result.diagnostic.fallback_order
    assert len(result.ordered_regions) == len(refs)
    assert {id(item.region) for item in result.ordered_regions} == {
        id(item.region) for item in refs
    }


@pytest.mark.parametrize("page_id", PAGE_IDS)
def test_control_reproduces_frozen_a1_b0(page_id: str) -> None:
    page, regions, pixels = _load_case(page_id)
    candidate = run_post_v2_calibration_candidate(
        pixels,
        regions,
        page_height=page.height,
        config=CalibrationConfig(),
    )
    frozen = run_reading_order_v2_arm(
        pixels,
        regions,
        page_height=page.height,
        repository_sha=REPOSITORY_SHA,
        page_id=page_id,
        arm_id=ArmId.A1_B0_PANEL_ONLY,
    )
    assert _order(candidate) == tuple(item.region_id for item in frozen.ordered_regions)


@pytest.mark.parametrize("page_id", PAGE_IDS)
def test_b1_only_reproduces_frozen_combined(page_id: str) -> None:
    page, regions, pixels = _load_case(page_id)
    candidate = run_post_v2_calibration_candidate(
        pixels,
        regions,
        page_height=page.height,
        config=CalibrationConfig(b1_local_order=True),
    )
    frozen = run_reading_order_v2_arm(
        pixels,
        regions,
        page_height=page.height,
        repository_sha=REPOSITORY_SHA,
        page_id=page_id,
        arm_id=ArmId.A1_B1_COMBINED,
    )
    assert _order(candidate) == tuple(item.region_id for item in frozen.ordered_regions)


def test_c1_h11_boundary_center_becomes_uncertain() -> None:
    result = _run("H11", CalibrationConfig(c1_boundary_guard=True))
    assignment = next(
        item
        for item in result.diagnostic.assignments
        if item.region_id == "ro2h-H11-r002"
    )
    assert assignment.status == "unassigned"
    assert assignment.assigned_group_index is None
    assert assignment.reason == "no-guarded-center-containment"


def test_c1_h07_legitimate_near_border_assignment_is_preserved() -> None:
    control = _run("H07", CalibrationConfig())
    guarded = _run("H07", CalibrationConfig(c1_boundary_guard=True))
    assert all(item.status == "confident" for item in guarded.diagnostic.assignments)
    assert _order(guarded) == _order(control)


@pytest.mark.parametrize("page_id", ["H08", "H15"])
def test_c2_interleaves_observed_uncertain_singleton(page_id: str) -> None:
    result = _run(page_id, CalibrationConfig(c2_uncertain_relations=True))
    assert _order(result) == _ground_truth_order(page_id)
    assert result.diagnostic.relation_edges
    assert result.diagnostic.fallback_reason is None


def test_c2_one_sided_evidence_keeps_uncertain_node_unrelated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_boxes = (
        PanelBox(0, 0, 100, 100),
        PanelBox(0, 200, 100, 300),
    )
    region_boxes = (
        (20, 20, 40, 40),
        (20, 220, 40, 240),
        (150, 350, 250, 450),
    )
    refs, first = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    _, second = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    proposed, status = _relation_status(
        panel_boxes=panel_boxes,
        refs=refs,
        groups=((0,), (1,)),
        region_index=2,
    )
    assignment = first.diagnostic.assignments[2]
    assert status == "rejected-insufficient-two-sided-relations"
    assert proposed == ()
    assert assignment.status == "unassigned"
    assert assignment.assigned_group_index is None
    assert first.diagnostic.relation_edges == ()
    assert first.diagnostic.fallback_reason is None
    assert first.diagnostic == second.diagnostic
    assert _order(first) == _order(second)


def test_c2_non_unique_slot_rejects_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_boxes = (
        PanelBox(0, 0, 100, 100),
        PanelBox(0, 150, 100, 250),
        PanelBox(0, 300, 100, 400),
    )
    region_boxes = (
        (20, 20, 40, 40),
        (20, 170, 40, 190),
        (20, 320, 40, 340),
        (-60, 110, 40, 290),
    )
    refs, result = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    proposed, status = _relation_status(
        panel_boxes=panel_boxes,
        refs=refs,
        groups=((0,), (1,), (2,)),
        region_index=3,
    )
    assignment = result.diagnostic.assignments[3]
    assert status == "rejected-non-unique-slot"
    assert proposed == ()
    assert assignment.status == "unassigned"
    assert assignment.assigned_group_index is None
    assert result.diagnostic.relation_edges == ()
    assert result.diagnostic.fallback_reason is None


def test_c2_relation_conflict_falls_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_boxes = (
        PanelBox(0, 0, 100, 100),
        PanelBox(300, 200, 400, 300),
    )
    region_boxes = (
        (20, 20, 40, 40),
        (320, 220, 340, 240),
        (150, 50, 250, 250),
    )
    refs, result = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    proposed, status = _relation_status(
        panel_boxes=panel_boxes,
        refs=refs,
        groups=((0,), (1,)),
        region_index=2,
    )
    assert proposed == ()
    assert status == "conflict"
    assert result.diagnostic.assignments[2].status == "unassigned"
    assert result.diagnostic.fallback_reason == "uncertain-relation-conflict"
    assert result.diagnostic.relation_edges == ()
    assert _order(result) == result.diagnostic.fallback_order
    assert not result.diagnostic.used_panel_evidence


def test_c2_graph_cycle_from_accepted_uncertain_relations_falls_back_whole_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_boxes = (
        PanelBox(0, 0, 100, 100),
        PanelBox(80, 80, 180, 180),
    )
    region_boxes = (
        (10, 10, 30, 30),
        (150, 150, 170, 170),
        (300, 300, 340, 340),
        (400, 400, 440, 440),
    )

    def cyclic_relation_edges(**kwargs: Any) -> tuple[tuple[tuple[int, int, str], ...], str]:
        region_index = int(kwargs["region_index"])
        node = int(kwargs["uncertain_node"])
        if region_index == 2:
            return ((0, node, "synthetic-cycle"), (node, 1, "synthetic-cycle")), "accepted"
        return ((1, node, "synthetic-cycle"), (node, 0, "synthetic-cycle")), "accepted"

    monkeypatch.setattr(candidate_module, "_uncertain_relation_edges", cyclic_relation_edges)
    refs, result = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert result.diagnostic.fallback_reason == "precedence-cycle"
    assert len(result.diagnostic.relation_edges) == 4
    assert _order(result) == result.diagnostic.fallback_order
    assert result.diagnostic.final_order == result.diagnostic.fallback_order
    assert not result.diagnostic.used_panel_evidence
    assert len(result.ordered_regions) == len(refs)
    assert {id(item.region) for item in result.ordered_regions} == {
        id(item.region) for item in refs
    }


def test_c2_multiple_uncertain_nodes_accept_independent_unique_slots_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_boxes = (
        PanelBox(0, 0, 100, 100),
        PanelBox(0, 200, 100, 300),
        PanelBox(0, 400, 100, 500),
    )
    region_boxes = (
        (20, 20, 40, 40),
        (20, 220, 40, 240),
        (20, 420, 40, 440),
        (20, 110, 40, 190),
        (20, 310, 40, 390),
    )
    refs, first = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    _, second = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert first.diagnostic.assignments[3].status == "unassigned"
    assert first.diagnostic.assignments[4].status == "unassigned"
    assert first.diagnostic.assignments[3].assigned_group_index is None
    assert first.diagnostic.assignments[4].assigned_group_index is None
    assert first.diagnostic.fallback_reason is None
    assert first.diagnostic.node_order == ("g000", "u003", "g001", "u004", "g002")
    assert _order(first) == (
        refs[0].region_id,
        refs[3].region_id,
        refs[1].region_id,
        refs[4].region_id,
        refs[2].region_id,
    )
    assert first.diagnostic == second.diagnostic
    assert _order(first) == _order(second)


def test_c2_hard_panel_precedence_cannot_be_overridden_by_uncertain_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel_boxes = (
        PanelBox(0, 0, 100, 100),
        PanelBox(0, 200, 100, 300),
    )
    region_boxes = (
        (20, 20, 40, 40),
        (20, 220, 40, 240),
        (300, 120, 340, 160),
    )

    def reverse_hard_edge(**kwargs: Any) -> tuple[tuple[tuple[int, int, str], ...], str]:
        node = int(kwargs["uncertain_node"])
        return ((1, node, "synthetic-unsafe"), (node, 0, "synthetic-unsafe")), "accepted"

    monkeypatch.setattr(candidate_module, "_uncertain_relation_edges", reverse_hard_edge)
    _, result = _run_synthetic_panels(
        monkeypatch,
        panel_boxes=panel_boxes,
        region_boxes=region_boxes,
        config=CalibrationConfig(c2_uncertain_relations=True),
    )
    assert result.diagnostic.fallback_reason == "precedence-cycle"
    relation_pairs = tuple(
        (edge.source_node, edge.target_node)
        for edge in result.diagnostic.relation_edges
    )
    assert relation_pairs == (
        ("g001", "u002"),
        ("u002", "g000"),
    )
    assert _order(result) == result.diagnostic.fallback_order
    assert not result.diagnostic.used_panel_evidence


@pytest.mark.parametrize("page_id", ["H10", "H16"])
def test_c3_recovers_merged_frames(page_id: str) -> None:
    recovered = _run(page_id, CalibrationConfig(c3_merged_frame_recovery=True))
    assert recovered.diagnostic.segmentation_reliable, (
        page_id,
        recovered.diagnostic.recovery_reason,
        recovered.diagnostic.segmentation_boxes,
    )
    assert recovered.diagnostic.segmentation_reason == "recovered-merged-frame"
    assert (
        recovered.diagnostic.recovery_reason
        == "accepted-strong-anchor-plus-occlusion-supported-frame"
    )
    assert len(recovered.diagnostic.segmentation_boxes) == 2


@pytest.mark.parametrize("page_id", ["H10", "H16"])
def test_c1_c2_c3_interleaves_ambiguous_overlap_bridge(page_id: str) -> None:
    result = _run(page_id, FULL_PANEL_FIX)
    assert _order(result) == _ground_truth_order(page_id), (
        page_id,
        result.diagnostic.recovery_reason,
        result.diagnostic.segmentation_boxes,
        result.diagnostic.assignments,
        result.diagnostic.relation_edges,
    )
    assert any(
        edge.rule == "validated-overlap-bridge-right-before-left"
        for edge in result.diagnostic.relation_edges
    )


def test_c3_h13_multiple_complete_internal_frames_fail_closed() -> None:
    control = _run("H13", CalibrationConfig())
    recovered = _run("H13", CalibrationConfig(c3_merged_frame_recovery=True))
    assert not recovered.diagnostic.segmentation_reliable
    assert recovered.diagnostic.recovery_reason == "rejected-multiple-strong-frame-candidates"
    assert recovered.diagnostic.assignments == ()
    assert _order(recovered) == _order(control)


def test_c3_does_not_invent_second_frame_from_single_clean_frame() -> None:
    import cv2

    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (100, 100), (900, 900), (30, 30, 30), 12)

    refs = _synthetic_refs(
        (650, 200, 700, 400),
        (300, 500, 350, 700),
    )
    result = run_post_v2_calibration_candidate(
        pixels,
        refs,
        page_height=1000,
        config=CalibrationConfig(c3_merged_frame_recovery=True),
    )
    assert not result.diagnostic.segmentation_reliable
    assert result.diagnostic.recovery_reason.startswith("rejected-")
    assert _order(result) == result.diagnostic.fallback_order


def test_c3_zero_strong_anchor_falls_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = (
        (200.0, 200.0, 500.0, 200.0),
        (200.0, 800.0, 500.0, 800.0),
        (200.0, 200.0, 200.0, 500.0),
        (800.0, 200.0, 800.0, 500.0),
    )
    refs, first = _run_synthetic_c3(monkeypatch, lines=lines)
    _, second = _run_synthetic_c3(monkeypatch, lines=lines)
    _assert_exact_synthetic_fallback(refs, first)
    assert first.diagnostic.recovery_reason == "rejected-no-unique-strong-frame-anchor"
    assert first.diagnostic == second.diagnostic


def test_c3_multiple_strong_anchors_fall_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = _complete_frame_lines(PanelBox(150, 150, 450, 450)) + _complete_frame_lines(
        PanelBox(550, 550, 850, 850)
    )
    refs, first = _run_synthetic_c3(monkeypatch, lines=lines)
    _, second = _run_synthetic_c3(monkeypatch, lines=lines)
    _assert_exact_synthetic_fallback(refs, first)
    assert first.diagnostic.recovery_reason == "rejected-multiple-strong-frame-candidates"
    assert first.diagnostic == second.diagnostic


def test_c3_zero_occlusion_supported_companions_fall_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = _complete_frame_lines(PanelBox(400, 300, 700, 700))
    refs, first = _run_synthetic_c3(monkeypatch, lines=lines)
    _, second = _run_synthetic_c3(monkeypatch, lines=lines)
    _assert_exact_synthetic_fallback(refs, first)
    assert (
        first.diagnostic.recovery_reason
        == "rejected-ambiguous-or-missing-occlusion-supported-frame"
    )
    assert first.diagnostic == second.diagnostic


def test_c3_multiple_occlusion_supported_companions_fall_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = PanelBox(400, 300, 700, 700)
    lines = _complete_frame_lines(anchor) + (
        (100.0, 100.0, 500.0, 100.0),
        (100.0, 500.0, 500.0, 500.0),
        (100.0, 100.0, 100.0, 500.0),
        (500.0, 100.0, 500.0, 300.0),
        (100.0, 900.0, 500.0, 900.0),
        (100.0, 500.0, 100.0, 900.0),
        (500.0, 700.0, 500.0, 900.0),
    )
    refs, first = _run_synthetic_c3(monkeypatch, lines=lines)
    _, second = _run_synthetic_c3(monkeypatch, lines=lines)
    _assert_exact_synthetic_fallback(refs, first)
    assert (
        first.diagnostic.recovery_reason
        == "rejected-ambiguous-or-missing-occlusion-supported-frame"
    )
    assert first.diagnostic == second.diagnostic


def test_c3_invalid_recovered_topology_falls_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = PanelBox(400, 300, 700, 700)
    lines = _complete_frame_lines(anchor) + (
        (200.0, 200.0, 600.0, 200.0),
        (200.0, 600.0, 600.0, 600.0),
        (200.0, 200.0, 200.0, 600.0),
        (600.0, 200.0, 600.0, 300.0),
    )
    refs, first = _run_synthetic_c3(monkeypatch, lines=lines)
    _, second = _run_synthetic_c3(monkeypatch, lines=lines)
    _assert_exact_synthetic_fallback(refs, first)
    assert first.diagnostic.recovery_reason == "rejected-ambiguous-overlap"
    assert first.diagnostic == second.diagnostic


def test_c3_insufficient_visible_span_falls_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    merged = PanelBox(100, 100, 900, 900)
    anchor = PanelBox(400, 300, 700, 700)
    span_box = PanelBox(200, 250, 480, 650)
    span_hypothesis = candidate_module._FrameHypothesis(
        box=span_box,
        coverages=(1.0, 1.0, 1.0, 0.375),
        top=candidate_module._AxisCluster(250.0, ((200.0, 480.0),)),
        bottom=candidate_module._AxisCluster(650.0, ((200.0, 480.0),)),
        left=candidate_module._AxisCluster(200.0, ((250.0, 650.0),)),
        right=candidate_module._AxisCluster(480.0, ((250.0, 400.0),)),
    )
    visible = candidate_module._visible_side_coverages(
        span_hypothesis,
        span_box,
        anchor,
        merged,
    )
    assert visible is None

    monkeypatch.setattr(
        candidate_module,
        "_visible_side_coverages",
        lambda *_args, **_kwargs: visible,
    )
    lines = _complete_frame_lines(anchor) + (
        (200.0, 180.0, 600.0, 180.0),
        (200.0, 600.0, 600.0, 600.0),
        (200.0, 180.0, 200.0, 600.0),
        (600.0, 240.0, 600.0, 400.0),
    )
    refs, first = _run_synthetic_c3(monkeypatch, lines=lines, merged=merged)
    _, second = _run_synthetic_c3(monkeypatch, lines=lines, merged=merged)
    _assert_exact_synthetic_fallback(refs, first)
    assert (
        first.diagnostic.recovery_reason
        == "rejected-ambiguous-or-missing-occlusion-supported-frame"
    )
    assert first.diagnostic == second.diagnostic


def test_c3_insufficient_visible_coverage_falls_back_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = _complete_frame_lines(PanelBox(400, 300, 700, 700)) + (
        (200.0, 180.0, 600.0, 180.0),
        (200.0, 600.0, 600.0, 600.0),
        (200.0, 180.0, 200.0, 600.0),
        (600.0, 240.0, 600.0, 400.0),
    )
    refs, first = _run_synthetic_c3(monkeypatch, lines=lines)
    _, second = _run_synthetic_c3(monkeypatch, lines=lines)
    _assert_exact_synthetic_fallback(refs, first)
    assert (
        first.diagnostic.recovery_reason
        == "rejected-ambiguous-or-missing-occlusion-supported-frame"
    )
    assert first.diagnostic == second.diagnostic


def test_full_candidate_is_deterministic_and_preserves_region_objects() -> None:
    page, regions, pixels = _load_case("H16")
    before = tuple(id(item.region) for item in regions)
    first = run_post_v2_calibration_candidate(
        pixels,
        regions,
        page_height=page.height,
        config=FULL_PANEL_FIX,
    )
    second = run_post_v2_calibration_candidate(
        pixels,
        regions,
        page_height=page.height,
        config=FULL_PANEL_FIX,
    )
    assert first.diagnostic == second.diagnostic
    assert _order(first) == _order(second)
    assert {id(item.region) for item in first.ordered_regions} == set(before)
    assert tuple(id(item.region) for item in regions) == before


def test_full_panel_fix_has_no_clean_control_regression_against_control() -> None:
    clean_pages: list[str] = []
    for page_id in PAGE_IDS:
        annotation = Path(CORPUS_ROOT) / "annotations" / f"{page_id}.json"
        payload = json.loads(annotation.read_text(encoding="utf-8"))
        if "clean-control" in payload.get("layoutTags", []):
            clean_pages.append(page_id)

    for page_id in clean_pages:
        control = _run(page_id, CalibrationConfig())
        candidate = _run(page_id, FULL_PANEL_FIX)
        expected = _ground_truth_order(page_id)
        if _order(control) == expected:
            assert _order(candidate) == expected