from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

import mangasensei.ocr.diagnostics.reading_order_post_v2_calibration as candidate_module
from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    CalibrationResult,
    run_post_v2_calibration_candidate,
)
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ExperimentRegion
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation
from scripts.reading_order_post_v2_qualification import DIAGNOSTIC_SCHEMA_VERSION
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import build_exercise_report_v3
from scripts.reading_order_post_v2_qualification.run_arm import _config as production_arm_config

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_ARM_PATH = REPO_ROOT / "scripts" / "reading_order_post_v2_qualification" / "run_arm.py"
PAGE_ID = "Q901"
EXECUTION_SHA = "0" * 40
PRODUCTION_DIAGNOSTIC_KEYS = {
    "schemaVersion",
    "experimentArm",
    "executionSha",
    "pageId",
    "preSegmentation",
    "segmentation",
    "recoveryReason",
    "assignments",
    "relationEdges",
    "nodeOrder",
    "fallbackReason",
    "usedPanelEvidence",
    "fallbackOrder",
    "finalOrder",
    "regionDirections",
    "regionIntegrity",
}


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


def _serialize_production_shape(
    *,
    result: CalibrationResult,
    refs: tuple[ExperimentRegion, ...],
    pre_segmentation: PanelSegmentation,
    arm: ArmId,
) -> dict[str, object]:
    assignments: list[dict[str, object]] = []
    for region_index, assignment in enumerate(result.diagnostic.assignments):
        ref = refs[region_index]
        assignments.append(
            {
                "regionId": assignment.region_id,
                "sourceIndex": ref.source_index,
                "candidateGroupIndices": list(assignment.candidate_group_indices),
                "status": assignment.status,
                "reason": assignment.reason,
                "assignedGroupIndex": assignment.assigned_group_index,
                "uncertainNodeLabel": (
                    f"u{region_index:03d}" if assignment.assigned_group_index is None else None
                ),
            }
        )
    return {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "experimentArm": arm.value,
        "executionSha": EXECUTION_SHA,
        "pageId": PAGE_ID,
        "preSegmentation": {
            "reliable": pre_segmentation.reliable,
            "reason": pre_segmentation.reason,
            "boxCount": len(pre_segmentation.boxes),
            "boxes": [
                {"x1": box.x1, "y1": box.y1, "x2": box.x2, "y2": box.y2}
                for box in pre_segmentation.boxes
            ],
        },
        "segmentation": {
            "reliable": result.diagnostic.segmentation_reliable,
            "reason": result.diagnostic.segmentation_reason,
            "boxes": [
                {"x1": box.x1, "y1": box.y1, "x2": box.x2, "y2": box.y2}
                for box in result.diagnostic.segmentation_boxes
            ],
        },
        "recoveryReason": result.diagnostic.recovery_reason,
        "assignments": assignments,
        "relationEdges": [
            {
                "sourceNode": edge.source_node,
                "targetNode": edge.target_node,
                "rule": edge.rule,
            }
            for edge in result.diagnostic.relation_edges
        ],
        "nodeOrder": list(result.diagnostic.node_order),
        "fallbackReason": result.diagnostic.fallback_reason,
        "usedPanelEvidence": result.diagnostic.used_panel_evidence,
        "fallbackOrder": list(result.diagnostic.fallback_order),
        "finalOrder": list(result.diagnostic.final_order),
        "regionDirections": {
            ref.region_id: str(getattr(ref.region, "direction", "")) for ref in refs
        },
        "regionIntegrity": {
            "countPreserved": True,
            "objectIdentitySetPreserved": True,
            "contentConfidenceGeometryPreserved": True,
        },
    }


def _run_with_panels(
    monkeypatch: pytest.MonkeyPatch,
    *,
    panel_boxes: tuple[PanelBox, ...],
    region_boxes: tuple[tuple[int, int, int, int], ...],
    arm: ArmId,
    directions: tuple[str, ...] | None = None,
) -> dict[str, object]:
    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    refs = _refs(region_boxes, directions=directions)
    pre = PanelSegmentation(panel_boxes, True, "reliable")
    monkeypatch.setattr(candidate_module, "segment_panel_groups", lambda _pixels: pre)
    result = run_post_v2_calibration_candidate(
        pixels,
        refs,
        page_height=1000,
        config=production_arm_config(arm),
    )
    return _serialize_production_shape(result=result, refs=refs, pre_segmentation=pre, arm=arm)


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
    arm: ArmId,
) -> dict[str, object]:
    pixels = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    refs = _refs(((760, 150, 800, 230), (180, 740, 230, 820)))
    merged = PanelBox(100, 100, 900, 900)
    pre = PanelSegmentation((merged,), False, "fewer-than-two-groups")
    monkeypatch.setattr(candidate_module, "segment_panel_groups", lambda _pixels: pre)
    monkeypatch.setattr(candidate_module, "_line_segments", lambda _gray: lines)
    result = run_post_v2_calibration_candidate(
        pixels,
        refs,
        page_height=1000,
        config=production_arm_config(arm),
    )
    return _serialize_production_shape(result=result, refs=refs, pre_segmentation=pre, arm=arm)


def _page(slice_name: str, pair: tuple[str, str], region_count: int) -> PageGroundTruth:
    region_ids = tuple(f"reachability-r{index}" for index in range(region_count))
    return PageGroundTruth(
        page_id=PAGE_ID,
        reading_order=region_ids,
        unscored_region_ids=(),
        qualification_pairs=(QualificationPair("p1", pair[0], pair[1], (slice_name,)),),
        layout_tags=(),
    )


def _assert_count(
    *,
    metric: str,
    slice_name: str,
    pair: tuple[str, str],
    region_count: int,
    diagnostics: dict[ArmId, dict[str, object]],
) -> None:
    page = _page(slice_name, pair, region_count)
    wrapped = {arm: {PAGE_ID: diagnostic} for arm, diagnostic in diagnostics.items()}
    report = build_exercise_report_v3(annotations=(page,), diagnostics=wrapped)
    assert report.counts[metric].count == 1


def _assert_repeat(first: dict[str, object], second: dict[str, object]) -> None:
    assert first == second


def test_v3_reachability_c1_candidate_to_production_diagnostic_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100))
    regions = ((-10, 40, 10, 60), (20, 20, 40, 40), (220, 20, 240, 40))
    control = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        arm=ArmId.CONTROL,
    )
    first = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        arm=ArmId.C1_ONLY,
    )
    repeat = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        arm=ArmId.C1_ONLY,
    )
    _assert_repeat(first, repeat)
    _assert_count(
        metric="c1_guarded_pairs",
        slice_name="c1-boundary-positive",
        pair=("reachability-r0", "reachability-r1"),
        region_count=3,
        diagnostics={ArmId.CONTROL: control, ArmId.C1_ONLY: first},
    )


@pytest.mark.parametrize(
    ("metric", "slice_name", "panels", "regions", "pair"),
    [
        (
            "c2_gutter_pairs",
            "c2-gutter-bridge",
            (PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100)),
            ((20, 20, 40, 40), (220, 20, 240, 40), (140, 40, 160, 60)),
            ("reachability-r0", "reachability-r2"),
        ),
        (
            "c2_overlap_pairs",
            "c2-ambiguous-overlap-bridge",
            (PanelBox(0, 0, 120, 100), PanelBox(100, 0, 220, 100)),
            ((20, 20, 40, 40), (180, 20, 200, 40), (105, 40, 115, 60)),
            ("reachability-r0", "reachability-r2"),
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
            ("reachability-r0", "reachability-r3"),
        ),
    ],
)
def test_v3_reachability_c2_relation_candidate_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    slice_name: str,
    panels: tuple[PanelBox, ...],
    regions: tuple[tuple[int, int, int, int], ...],
    pair: tuple[str, str],
) -> None:
    def run(arm: ArmId) -> dict[str, object]:
        return _run_with_panels(
            monkeypatch,
            panel_boxes=panels,
            region_boxes=regions,
            arm=arm,
        )

    first = run(ArmId.C2_ONLY)
    _assert_repeat(first, run(ArmId.C2_ONLY))
    diagnostics = {
        arm: first if arm is ArmId.C2_ONLY else run(arm)
        for arm in (ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    }
    _assert_count(
        metric=metric,
        slice_name=slice_name,
        pair=pair,
        region_count=len(regions),
        diagnostics=diagnostics,
    )


def test_v3_reachability_c2_fail_closed_candidate_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300))
    regions = ((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450))
    first = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        arm=ArmId.C2_ONLY,
    )
    repeat = _run_with_panels(
        monkeypatch,
        panel_boxes=panels,
        region_boxes=regions,
        arm=ArmId.C2_ONLY,
    )
    _assert_repeat(first, repeat)
    _assert_count(
        metric="c2_fail_closed_no_relation_pairs",
        slice_name="c2-one-sided-non-unique-fail-closed",
        pair=("reachability-r0", "reachability-r2"),
        region_count=3,
        diagnostics={ArmId.C2_ONLY: first},
    )


def test_v3_reachability_c2_conflict_candidate_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(300, 200, 400, 300))
    regions = ((20, 20, 40, 40), (320, 220, 340, 240), (150, 50, 250, 250))

    def run(arm: ArmId) -> dict[str, object]:
        return _run_with_panels(
            monkeypatch,
            panel_boxes=panels,
            region_boxes=regions,
            arm=arm,
        )

    first = run(ArmId.C2_ONLY)
    _assert_repeat(first, run(ArmId.C2_ONLY))
    _assert_count(
        metric="c2_conflict_cycle_fallback_pairs",
        slice_name="c2-conflict-cycle-safety",
        pair=("reachability-r0", "reachability-r1"),
        region_count=3,
        diagnostics={ArmId.CONTROL: run(ArmId.CONTROL), ArmId.C2_ONLY: first},
    )


def test_v3_reachability_c3_positive_candidate_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor = PanelBox(400, 300, 700, 700)
    lines = _complete_frame_lines(anchor) + (
        (100.0, 100.0, 500.0, 100.0),
        (100.0, 500.0, 500.0, 500.0),
        (100.0, 100.0, 100.0, 500.0),
        (500.0, 100.0, 500.0, 300.0),
    )

    def run(arm: ArmId) -> dict[str, object]:
        return _run_c3(monkeypatch, lines=lines, arm=arm)

    first = run(ArmId.C3_ONLY)
    _assert_repeat(first, run(ArmId.C3_ONLY))
    _assert_count(
        metric="c3_positive_pairs",
        slice_name="c3-positive-recovery",
        pair=("reachability-r0", "reachability-r1"),
        region_count=2,
        diagnostics={
            ArmId.C3_ONLY: first,
            ArmId.C1_C2_C3: run(ArmId.C1_C2_C3),
            ArmId.C1_C2_C3_B1: run(ArmId.C1_C2_C3_B1),
        },
    )


def test_v3_reachability_c3_rejection_candidate_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = (
        (200.0, 200.0, 500.0, 200.0),
        (200.0, 800.0, 500.0, 800.0),
        (200.0, 200.0, 200.0, 500.0),
        (800.0, 200.0, 800.0, 500.0),
    )

    def run(arm: ArmId) -> dict[str, object]:
        return _run_c3(monkeypatch, lines=lines, arm=arm)

    first = run(ArmId.C3_ONLY)
    _assert_repeat(first, run(ArmId.C3_ONLY))
    _assert_count(
        metric="c3_rejection_pages",
        slice_name="c3-invalid-topology-negative",
        pair=("reachability-r0", "reachability-r1"),
        region_count=2,
        diagnostics={
            ArmId.C3_ONLY: first,
            ArmId.C1_C2_C3: run(ArmId.C1_C2_C3),
            ArmId.C1_C2_C3_B1: run(ArmId.C1_C2_C3_B1),
        },
    )


@pytest.mark.parametrize(
    ("metric", "slice_name", "directions"),
    [
        ("b1_horizontal_pairs", "b1-horizontal", ("h", "h", "v")),
        ("b1_vertical_pairs", "b1-vertical", ("v", "v", "h")),
        ("b1_mixed_pairs", "b1-mixed-orientation", ("h", "v", "h")),
    ],
)
def test_v3_reachability_b1_candidate_to_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    metric: str,
    slice_name: str,
    directions: tuple[str, ...],
) -> None:
    panels = (PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100))
    regions = ((220, 20, 240, 40), (250, 20, 270, 40), (20, 20, 40, 40))

    def run(arm: ArmId) -> dict[str, object]:
        return _run_with_panels(
            monkeypatch,
            panel_boxes=panels,
            region_boxes=regions,
            directions=directions,
            arm=arm,
        )

    first = run(ArmId.B1_ONLY)
    _assert_repeat(first, run(ArmId.B1_ONLY))
    _assert_count(
        metric=metric,
        slice_name=slice_name,
        pair=("reachability-r0", "reachability-r1"),
        region_count=3,
        diagnostics={ArmId.B1_ONLY: first, ArmId.C1_C2_C3_B1: run(ArmId.C1_C2_C3_B1)},
    )


def test_v3_reachability_serializer_tracks_run_arm_production_shape() -> None:
    tree = ast.parse(RUN_ARM_PATH.read_text(encoding="utf-8"))
    diagnostic_keys: set[str] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "diagnostic" for target in node.targets
        ):
            if isinstance(node.value, ast.Dict):
                diagnostic_keys = {
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                break
    assert diagnostic_keys == PRODUCTION_DIAGNOSTIC_KEYS


def test_v3_reachability_cases_do_not_patch_candidate_mechanism_functions() -> None:
    forbidden = {
        "_assignment_observations",
        "_uncertain_relation_edges",
        "_recover_merged_frames",
        "_b1_local_order",
    }
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    patched: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        function = node.func
        if not (
            isinstance(function, ast.Attribute)
            and isinstance(function.value, ast.Name)
            and function.value.id == "monkeypatch"
            and function.attr == "setattr"
        ):
            continue
        target = node.args[1]
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            patched.add(target.value)
    assert forbidden.isdisjoint(patched)
