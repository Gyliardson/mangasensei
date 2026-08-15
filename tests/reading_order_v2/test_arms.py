from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

import mangasensei.ocr.diagnostics.reading_order_v2 as v2
import mangasensei.ocr.reading_order as production
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId, ExperimentRegion
from mangasensei.ocr.reading_order import (
    PanelBox,
    PanelSegmentation,
    _OverlapEvidence,
    _PanelPrecedenceEdge,
)


@dataclass(slots=True)
class Region:
    xyxy: tuple[int, int, int, int]
    direction: str = "v"
    text: str = "fixture"
    prob: float = 0.5
    angle: float = 0.0

    @property
    def min_rect(self) -> np.ndarray:
        x1, y1, x2, y2 = self.xyxy
        return np.array(
            [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]],
            dtype=np.int64,
        )


def _run(
    monkeypatch: pytest.MonkeyPatch,
    boxes: tuple[PanelBox, ...],
    regions: list[Region],
    arm: ArmId,
):
    segmentation = PanelSegmentation(boxes, True, "reliable")
    monkeypatch.setattr(production, "segment_panel_groups", lambda pixels: segmentation)
    monkeypatch.setattr(v2, "segment_panel_groups", lambda pixels: segmentation)
    refs = tuple(
        ExperimentRegion(f"r{index}", index, region)
        for index, region in enumerate(regions)
    )
    return v2.run_reading_order_v2_arm(
        np.zeros((1000, 1000, 3), dtype=np.uint8),
        refs,
        page_height=1000,
        repository_sha="2" * 40,
        page_id="unit",
        arm_id=arm,
    )


def test_a1_keeps_unassigned_region_as_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(500, 0, 1000, 450), PanelBox(0, 0, 450, 450))
    regions = [
        Region((700, 100, 800, 300)),
        Region((100, 100, 200, 300)),
        Region((450, 700, 550, 850)),
    ]
    result = _run(monkeypatch, boxes, regions, ArmId.A1_B0_PANEL_ONLY)
    assert result.diagnostic.panel_evidence_mode.value == "partial"
    assert {item.region_id for item in result.ordered_regions} == {"r0", "r1", "r2"}
    uncertain = next(item for item in result.diagnostic.regions if item.region_id == "r2")
    assert uncertain.assignment_status.value == "unassigned"
    assert uncertain.assigned_group_id is None
    assert uncertain.local_ordering_mode == "singleton"


def test_a1_keeps_ambiguous_region_without_coercion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (
        PanelBox(650, 0, 1000, 450),
        PanelBox(0, 0, 350, 450),
        PanelBox(300, 500, 700, 900),
        PanelBox(500, 500, 900, 900),
    )
    regions = [
        Region((750, 100, 850, 300)),
        Region((100, 100, 200, 300)),
        Region((550, 600, 650, 750)),
    ]
    result = _run(monkeypatch, boxes, regions, ArmId.A1_B0_PANEL_ONLY)
    ambiguous = next(item for item in result.diagnostic.regions if item.region_id == "r2")
    assert ambiguous.assignment_status.value == "ambiguous"
    assert ambiguous.ambiguity_count == 2
    assert ambiguous.assigned_group_id is None
    assert set(ambiguous.candidate_group_ids) == {"g002", "g003"}


def test_a1_falls_back_with_fewer_than_two_confident_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(500, 0, 1000, 450), PanelBox(0, 0, 450, 450))
    regions = [Region((700, 100, 800, 300)), Region((450, 700, 550, 850))]
    result = _run(monkeypatch, boxes, regions, ArmId.A1_B0_PANEL_ONLY)
    assert not result.diagnostic.used_panel_evidence
    assert result.diagnostic.fallback_reason == "insufficient-confident-panel-groups"


def test_a1_segmentation_failure_uses_page_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    segmentation = PanelSegmentation((), False, "fewer-than-two-groups")
    monkeypatch.setattr(v2, "segment_panel_groups", lambda pixels: segmentation)
    regions = [Region((700, 100, 800, 300)), Region((100, 100, 200, 300))]
    refs = tuple(ExperimentRegion(f"r{i}", i, region) for i, region in enumerate(regions))
    result = v2.run_reading_order_v2_arm(
        np.zeros((1000, 1000, 3), dtype=np.uint8),
        refs,
        page_height=1000,
        repository_sha="2" * 40,
        page_id="unit",
        arm_id=ArmId.A1_B0_PANEL_ONLY,
    )
    assert not result.diagnostic.used_panel_evidence
    assert result.diagnostic.fallback_reason == "fewer-than-two-groups"


def test_a1_precedence_cycle_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    boxes = (PanelBox(500, 0, 1000, 450), PanelBox(0, 0, 450, 450))

    def edge(source: int, target: int) -> _PanelPrecedenceEdge:
        return _PanelPrecedenceEdge(
            source,
            target,
            "same-level-right-before-left",
            _OverlapEvidence(0, 1),
            _OverlapEvidence(1, 1),
        )

    monkeypatch.setattr(
        v2,
        "_panel_precedence_edges",
        lambda boxes: (edge(0, 1), edge(1, 0)),
    )
    regions = [Region((700, 100, 800, 300)), Region((100, 100, 200, 300))]
    result = _run(monkeypatch, boxes, regions, ArmId.A1_B0_PANEL_ONLY)
    assert not result.diagnostic.used_panel_evidence
    assert result.diagnostic.fallback_reason == "precedence-cycle"


def test_a1_retains_empty_group_topology(monkeypatch: pytest.MonkeyPatch) -> None:
    boxes = (
        PanelBox(650, 0, 1000, 450),
        PanelBox(0, 0, 350, 450),
        PanelBox(0, 500, 1000, 950),
    )
    regions = [Region((750, 100, 850, 300)), Region((100, 100, 200, 300))]
    result = _run(monkeypatch, boxes, regions, ArmId.A1_B0_PANEL_ONLY)
    assert result.diagnostic.used_panel_evidence
    assert len(result.diagnostic.groups) == 3
    assert any(not group.confident_region_ids for group in result.diagnostic.groups)


def test_b1_horizontal_run_is_ltr_and_vertical_run_is_rtl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(0, 0, 700, 450), PanelBox(700, 0, 1000, 450))
    horizontal = [
        Region((400, 100, 550, 180), "h"),
        Region((100, 100, 250, 180), "h"),
        Region((800, 100, 870, 300), "v"),
    ]
    result = _run(monkeypatch, boxes, horizontal, ArmId.A0_B1_ORDER_ONLY)
    order = [item.region_id for item in result.ordered_regions]
    assert order.index("r1") < order.index("r0")

    vertical = [
        Region((400, 100, 480, 300), "v"),
        Region((100, 100, 180, 300), "v"),
        Region((800, 100, 870, 300), "v"),
    ]
    result = _run(monkeypatch, boxes, vertical, ArmId.A0_B1_ORDER_ONLY)
    order = [item.region_id for item in result.ordered_regions]
    assert order.index("r0") < order.index("r1")


def test_b1_mixed_subruns_follow_minimum_y_top(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(0, 0, 700, 450), PanelBox(700, 0, 1000, 450))
    horizontal_first = [
        Region((100, 80, 300, 150), "h"),
        Region((500, 120, 570, 300), "v"),
        Region((800, 100, 870, 300), "v"),
    ]
    result = _run(monkeypatch, boxes, horizontal_first, ArmId.A0_B1_ORDER_ONLY)
    order = [item.region_id for item in result.ordered_regions]
    assert order.index("r0") < order.index("r1")

    vertical_first = [
        Region((100, 140, 300, 210), "h"),
        Region((500, 90, 570, 300), "v"),
        Region((800, 100, 870, 300), "v"),
    ]
    result = _run(monkeypatch, boxes, vertical_first, ArmId.A0_B1_ORDER_ONLY)
    order = [item.region_id for item in result.ordered_regions]
    assert order.index("r1") < order.index("r0")


def test_b1_ambiguous_orientation_is_singleton(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(0, 0, 700, 450), PanelBox(700, 0, 1000, 450))
    regions = [
        Region((100, 100, 250, 180), "unknown"),
        Region((400, 100, 550, 180), "h"),
        Region((800, 100, 870, 300), "v"),
    ]
    result = _run(monkeypatch, boxes, regions, ArmId.A0_B1_ORDER_ONLY)
    diag = next(item for item in result.diagnostic.regions if item.region_id == "r0")
    assert diag.orientation_class.value == "ambiguous"
    assert diag.local_ordering_mode in {"singleton", "mixed"}


def test_combined_keeps_partial_region_and_horizontal_ltr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(0, 0, 650, 450), PanelBox(700, 0, 1000, 450))
    regions = [
        Region((400, 100, 550, 180), "h"),
        Region((100, 100, 250, 180), "h"),
        Region((800, 100, 870, 300), "v"),
        Region((400, 700, 500, 850), "v"),
    ]
    result = _run(monkeypatch, boxes, regions, ArmId.A1_B1_COMBINED)
    order = [item.region_id for item in result.ordered_regions]
    assert order.index("r1") < order.index("r0")
    assert set(order) == {"r0", "r1", "r2", "r3"}
    assert result.diagnostic.panel_evidence_mode.value == "partial"


def test_b1_tie_uses_external_source_index(monkeypatch: pytest.MonkeyPatch) -> None:
    boxes = (PanelBox(0, 0, 700, 450), PanelBox(700, 0, 1000, 450))
    regions = [
        Region((100, 100, 250, 180), "h"),
        Region((100, 100, 250, 180), "h"),
        Region((800, 100, 870, 300), "v"),
    ]
    result = _run(monkeypatch, boxes, regions, ArmId.A0_B1_ORDER_ONLY)
    order = [item.region_id for item in result.ordered_regions]
    assert order.index("r0") < order.index("r1")


def test_all_arms_preserve_runtime_region_objects_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(0, 0, 650, 450), PanelBox(700, 0, 1000, 450))
    regions = [
        Region((400, 100, 550, 180), "h"),
        Region((100, 100, 250, 180), "h"),
        Region((800, 100, 870, 300), "v"),
    ]
    before = [
        (id(region), region.xyxy, region.text, region.prob, region.angle, region.direction)
        for region in regions
    ]
    for arm in ArmId:
        result = _run(monkeypatch, boxes, regions, arm)
        assert {id(item.region) for item in result.ordered_regions} == {
            id(region) for region in regions
        }
        after = [
            (
                id(region),
                region.xyxy,
                region.text,
                region.prob,
                region.angle,
                region.direction,
            )
            for region in regions
        ]
        assert after == before
