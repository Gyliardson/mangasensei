from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import scripts.reading_order_post_v2_qualification.run_arm as run_arm_module
from PIL import Image
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import build_exercise_report_v3

import mangasensei.ocr.diagnostics.reading_order_post_v2_calibration as candidate_module
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation

EXECUTION_SHA = "f45facb2284d740df2f294800f705414e0ba465e"
PAGE_SIZE = 1000


@dataclass(frozen=True, slots=True)
class _SyntheticCase:
    metric: str
    slice_name: str
    arms: tuple[ArmId, ...]
    panels: tuple[PanelBox, ...]
    regions: tuple[tuple[int, int, int, int], ...]
    pair: tuple[str, str]
    mismatch_panels: tuple[PanelBox, ...]
    mismatch_regions: tuple[tuple[int, int, int, int], ...]
    segmentation_reliable: bool = True
    segmentation_reason: str = "reliable"
    lines: tuple[tuple[float, float, float, float], ...] | None = None
    mismatch_segmentation_reliable: bool = True
    mismatch_segmentation_reason: str = "reliable"
    mismatch_lines: tuple[tuple[float, float, float, float], ...] | None = None
    mismatch_pair: tuple[str, str] | None = None


def _complete_frame_lines(box: PanelBox) -> tuple[tuple[float, float, float, float], ...]:
    return (
        (float(box.x1), float(box.y1), float(box.x2), float(box.y1)),
        (float(box.x1), float(box.y2), float(box.x2), float(box.y2)),
        (float(box.x1), float(box.y1), float(box.x1), float(box.y2)),
        (float(box.x2), float(box.y1), float(box.x2), float(box.y2)),
    )


def _c3_positive_lines() -> tuple[tuple[float, float, float, float], ...]:
    anchor = PanelBox(400, 300, 700, 700)
    return _complete_frame_lines(anchor) + (
        (100.0, 100.0, 500.0, 100.0),
        (100.0, 500.0, 500.0, 500.0),
        (100.0, 100.0, 100.0, 500.0),
        (500.0, 100.0, 500.0, 300.0),
    )


def _c3_rejection_lines() -> tuple[tuple[float, float, float, float], ...]:
    return (
        (200.0, 200.0, 500.0, 200.0),
        (200.0, 800.0, 500.0, 800.0),
        (200.0, 200.0, 200.0, 500.0),
        (800.0, 200.0, 800.0, 500.0),
    )


def _cases() -> tuple[_SyntheticCase, ...]:
    c2_arms = (ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    c3_arms = (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1)
    b1_arms = (ArmId.B1_ONLY, ArmId.C1_C2_C3_B1)
    fail_closed_panels = (PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300))
    fail_closed_regions = ((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450))
    gutter_panels = (PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100))
    gutter_regions = ((20, 20, 40, 40), (220, 20, 240, 40), (140, 40, 160, 60))
    merged = (PanelBox(100, 100, 900, 900),)
    c3_regions = ((150, 150, 200, 230), (600, 550, 650, 630))
    return (
        _SyntheticCase(
            metric="c1_guarded_pairs",
            slice_name="c1-boundary-positive",
            arms=(ArmId.CONTROL, ArmId.C1_ONLY),
            panels=(PanelBox(100, 0, 200, 100), PanelBox(300, 0, 400, 100)),
            regions=((90, 40, 110, 60), (120, 20, 140, 40), (320, 20, 340, 40)),
            pair=("r1", "r2"),
            mismatch_panels=(PanelBox(100, 0, 200, 100), PanelBox(300, 0, 400, 100)),
            mismatch_regions=((92, 40, 112, 60), (120, 20, 140, 40), (320, 20, 340, 40)),
        ),
        _SyntheticCase(
            metric="c2_gutter_pairs",
            slice_name="c2-gutter-bridge",
            arms=c2_arms,
            panels=gutter_panels,
            regions=gutter_regions,
            pair=("r2", "r3"),
            mismatch_panels=fail_closed_panels,
            mismatch_regions=fail_closed_regions,
        ),
        _SyntheticCase(
            metric="c2_overlap_pairs",
            slice_name="c2-ambiguous-overlap-bridge",
            arms=c2_arms,
            panels=(PanelBox(0, 0, 120, 100), PanelBox(100, 0, 220, 100)),
            regions=((20, 20, 40, 40), (180, 20, 200, 40), (105, 40, 115, 60)),
            pair=("r2", "r3"),
            mismatch_panels=fail_closed_panels,
            mismatch_regions=fail_closed_regions,
        ),
        _SyntheticCase(
            metric="c2_pair_precedence_pairs",
            slice_name="c2-pair-precedence-slot",
            arms=c2_arms,
            panels=(
                PanelBox(0, 0, 100, 100),
                PanelBox(0, 200, 100, 300),
                PanelBox(0, 400, 100, 500),
            ),
            regions=(
                (20, 20, 40, 40),
                (20, 220, 40, 240),
                (20, 420, 40, 440),
                (20, 110, 40, 190),
            ),
            pair=("r1", "r4"),
            mismatch_panels=fail_closed_panels,
            mismatch_regions=fail_closed_regions,
            mismatch_pair=("r1", "r3"),
        ),
        _SyntheticCase(
            metric="c2_fail_closed_no_relation_pairs",
            slice_name="c2-one-sided-non-unique-fail-closed",
            arms=(ArmId.C2_ONLY,),
            panels=fail_closed_panels,
            regions=fail_closed_regions,
            pair=("r1", "r3"),
            mismatch_panels=gutter_panels,
            mismatch_regions=gutter_regions,
        ),
        _SyntheticCase(
            metric="c2_conflict_cycle_fallback_pairs",
            slice_name="c2-conflict-cycle-safety",
            arms=(ArmId.CONTROL, ArmId.C2_ONLY),
            panels=(PanelBox(0, 0, 100, 100), PanelBox(300, 200, 400, 300)),
            regions=((20, 20, 40, 40), (320, 220, 340, 240), (150, 50, 250, 250)),
            pair=("r1", "r3"),
            mismatch_panels=fail_closed_panels,
            mismatch_regions=fail_closed_regions,
        ),
        _SyntheticCase(
            metric="c3_positive_pairs",
            slice_name="c3-positive-recovery",
            arms=c3_arms,
            panels=merged,
            regions=c3_regions,
            pair=("r1", "r2"),
            segmentation_reliable=False,
            segmentation_reason="fewer-than-two-groups",
            lines=_c3_positive_lines(),
            mismatch_panels=merged,
            mismatch_regions=c3_regions,
            mismatch_segmentation_reliable=False,
            mismatch_segmentation_reason="fewer-than-two-groups",
            mismatch_lines=_c3_rejection_lines(),
        ),
        _SyntheticCase(
            metric="c3_rejection_pages",
            slice_name="c3-invalid-topology-negative",
            arms=c3_arms,
            panels=merged,
            regions=c3_regions,
            pair=("r1", "r2"),
            segmentation_reliable=False,
            segmentation_reason="fewer-than-two-groups",
            lines=_c3_rejection_lines(),
            mismatch_panels=merged,
            mismatch_regions=c3_regions,
            mismatch_segmentation_reliable=False,
            mismatch_segmentation_reason="fewer-than-two-groups",
            mismatch_lines=_c3_positive_lines(),
        ),
        _SyntheticCase(
            metric="b1_horizontal_pairs",
            slice_name="b1-horizontal",
            arms=b1_arms,
            panels=gutter_panels,
            regions=((220, 20, 260, 40), (250, 50, 290, 70), (20, 20, 40, 60)),
            pair=("r1", "r2"),
            mismatch_panels=gutter_panels,
            mismatch_regions=((220, 20, 260, 40), (20, 50, 60, 70), (250, 20, 270, 60)),
        ),
        _SyntheticCase(
            metric="b1_vertical_pairs",
            slice_name="b1-vertical",
            arms=b1_arms,
            panels=gutter_panels,
            regions=((220, 20, 240, 60), (250, 20, 270, 60), (20, 20, 60, 40)),
            pair=("r1", "r2"),
            mismatch_panels=gutter_panels,
            mismatch_regions=((220, 20, 240, 60), (20, 20, 40, 60), (250, 20, 290, 40)),
        ),
        _SyntheticCase(
            metric="b1_mixed_pairs",
            slice_name="b1-mixed-orientation",
            arms=b1_arms,
            panels=gutter_panels,
            regions=((220, 20, 260, 40), (250, 20, 270, 60), (20, 20, 60, 40)),
            pair=("r1", "r2"),
            mismatch_panels=gutter_panels,
            mismatch_regions=((220, 20, 260, 40), (20, 20, 40, 60), (250, 20, 290, 40)),
        ),
    )


def _quad(box: tuple[int, int, int, int]) -> list[list[int]]:
    x1, y1, x2, y2 = box
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _write_corpus(
    root: Path,
    *,
    page_id: str,
    region_boxes: tuple[tuple[int, int, int, int], ...],
) -> None:
    (root / "inputs").mkdir(parents=True)
    (root / "images").mkdir(parents=True)
    payload = {
        "schemaVersion": "reading-order-post-v2-input-v1",
        "pageId": page_id,
        "width": PAGE_SIZE,
        "height": PAGE_SIZE,
        "regions": [
            {
                "regionId": f"r{index + 1}",
                "sourceIndex": index,
                "lines": [_quad(box)],
                "angle": 0,
            }
            for index, box in enumerate(region_boxes)
        ],
    }
    (root / "inputs" / f"{page_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    Image.fromarray(np.full((PAGE_SIZE, PAGE_SIZE, 3), 255, dtype=np.uint8)).save(
        root / "images" / f"{page_id}.png"
    )


def _set_allowed_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    panels: tuple[PanelBox, ...],
    reliable: bool,
    reason: str,
    lines: tuple[tuple[float, float, float, float], ...] | None,
) -> None:
    segmentation = PanelSegmentation(panels, reliable, reason)
    monkeypatch.setattr(candidate_module, "segment_panel_groups", lambda _pixels: segmentation)
    monkeypatch.setattr(run_arm_module, "segment_panel_groups", lambda _pixels: segmentation)
    if lines is not None:
        monkeypatch.setattr(candidate_module, "_line_segments", lambda _gray: lines)


def _execute_diagnostics(
    *,
    corpus_root: Path,
    output_root: Path,
    page_id: str,
    arms: tuple[ArmId, ...],
    repeat: int,
) -> dict[ArmId, dict[str, dict[str, object]]]:
    diagnostics: dict[ArmId, dict[str, dict[str, object]]] = {}
    for arm in arms:
        diagnostic_path, _ = run_arm_module.execute_page(
            corpus_root=corpus_root,
            page_id=page_id,
            arm_id=arm,
            execution_sha=EXECUTION_SHA,
            repeat=repeat,
            output_root=output_root,
        )
        raw = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        diagnostics[arm] = {page_id: raw}
    return diagnostics


def _annotation(
    *,
    page_id: str,
    slice_name: str,
    region_count: int,
    pair: tuple[str, str],
) -> PageGroundTruth:
    return PageGroundTruth(
        page_id=page_id,
        reading_order=tuple(f"r{index + 1}" for index in range(region_count)),
        unscored_region_ids=(),
        qualification_pairs=(QualificationPair("p1", pair[0], pair[1], (slice_name,)),),
        layout_tags=(),
    )


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case.metric)
def test_v3_candidate_production_diagnostic_reaches_evaluator_deterministically(
    case: _SyntheticCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_root = tmp_path / "target"
    _write_corpus(target_root, page_id="Q901", region_boxes=case.regions)
    _set_allowed_seams(
        monkeypatch,
        panels=case.panels,
        reliable=case.segmentation_reliable,
        reason=case.segmentation_reason,
        lines=case.lines,
    )
    first = _execute_diagnostics(
        corpus_root=target_root,
        output_root=target_root / "output",
        page_id="Q901",
        arms=case.arms,
        repeat=1,
    )
    second = _execute_diagnostics(
        corpus_root=target_root,
        output_root=target_root / "output",
        page_id="Q901",
        arms=case.arms,
        repeat=2,
    )
    assert first == second

    page = _annotation(
        page_id="Q901",
        slice_name=case.slice_name,
        region_count=len(case.regions),
        pair=case.pair,
    )
    report = build_exercise_report_v3(annotations=(page,), diagnostics=first)
    assert report.counts[case.metric].count == 1

    mismatch_root = tmp_path / "mismatch"
    _write_corpus(mismatch_root, page_id="Q902", region_boxes=case.mismatch_regions)
    _set_allowed_seams(
        monkeypatch,
        panels=case.mismatch_panels,
        reliable=case.mismatch_segmentation_reliable,
        reason=case.mismatch_segmentation_reason,
        lines=case.mismatch_lines,
    )
    mismatch = _execute_diagnostics(
        corpus_root=mismatch_root,
        output_root=mismatch_root / "output",
        page_id="Q902",
        arms=case.arms,
        repeat=1,
    )
    mismatch_page = _annotation(
        page_id="Q902",
        slice_name=case.slice_name,
        region_count=len(case.mismatch_regions),
        pair=case.mismatch_pair or case.pair,
    )
    mismatch_report = build_exercise_report_v3(
        annotations=(mismatch_page,), diagnostics=mismatch
    )
    assert mismatch_report.counts[case.metric].count == 0


def test_v3_reachability_monkeypatch_seams_are_static_and_allowlisted() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    setattr_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "setattr"
    ]
    assert setattr_calls

    targets: list[str] = []
    for call in setattr_calls:
        assert isinstance(call.func, ast.Attribute)
        receiver = call.func.value
        assert (
            isinstance(receiver, ast.Name) and receiver.id == "monkeypatch"
        ), f"non-monkeypatch setattr call at line {call.lineno}"
        assert len(call.args) >= 2, (
            f"unresolved monkeypatch.setattr target at line {call.lineno}"
        )
        target = call.args[1]
        assert isinstance(target, ast.Constant) and isinstance(target.value, str), (
            f"dynamic monkeypatch.setattr target at line {call.lineno}"
        )
        targets.append(target.value)

    allowed = {"segment_panel_groups", "_line_segments"}
    forbidden = {
        "_assignment_observations",
        "_uncertain_relation_edges",
        "_recover_merged_frames",
        "_b1_local_order",
    }
    assert set(targets) == allowed
    assert forbidden.isdisjoint(targets)
