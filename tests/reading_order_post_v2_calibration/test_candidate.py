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

from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    CalibrationConfig,
    CalibrationResult,
    run_post_v2_calibration_candidate,
)
from mangasensei.ocr.diagnostics.reading_order_v2 import run_reading_order_v2_arm
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId, ExperimentRegion

REPOSITORY_SHA = "5" * 40
FULL_PANEL_FIX = CalibrationConfig(
    c1_boundary_guard=True,
    c2_uncertain_relations=True,
    c3_merged_frame_recovery=True,
)


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


@pytest.mark.parametrize("page_id", ["H10", "H16"])
def test_c3_recovers_merged_frames(page_id: str) -> None:
    recovered = _run(page_id, CalibrationConfig(c3_merged_frame_recovery=True))
    assert recovered.diagnostic.segmentation_reliable, (
        page_id,
        recovered.diagnostic.recovery_reason,
        recovered.diagnostic.segmentation_boxes,
    )
    assert recovered.diagnostic.segmentation_reason == "recovered-merged-frame"
    assert recovered.diagnostic.recovery_reason == "accepted-strong-four-side-frames"
    assert len(recovered.diagnostic.segmentation_boxes) >= 2


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


def test_c3_does_not_invent_second_frame_from_single_clean_frame() -> None:
    import cv2

    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    cv2.rectangle(pixels, (100, 100), (900, 900), (30, 30, 30), 12)

    class Region:
        def __init__(self, xyxy: tuple[int, int, int, int]) -> None:
            self.xyxy = xyxy
            self.direction = "v"

    refs = (
        ExperimentRegion("r0", 0, Region((650, 200, 700, 400))),
        ExperimentRegion("r1", 1, Region((300, 500, 350, 700))),
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
