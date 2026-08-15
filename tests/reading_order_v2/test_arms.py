from __future__ import annotations

import numpy as np

from mangasensei.ocr.diagnostics import reading_order_v2 as v2
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ExperimentRegion, ReadingOrderArm
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation


class Region:
    def __init__(self, xyxy: tuple[int, int, int, int], direction: str = "h") -> None:
        self.xyxy = xyxy
        self.direction = direction
        self.min_rect = (
            np.array(
                [
                    [xyxy[0], xyxy[1]],
                    [xyxy[2], xyxy[1]],
                    [xyxy[2], xyxy[3]],
                    [xyxy[0], xyxy[3]],
                ]
            ),
            0,
        )


def ref(
    index: int,
    xyxy: tuple[int, int, int, int],
    direction: str = "h",
) -> ExperimentRegion:
    return ExperimentRegion(f"r{index}", index, Region(xyxy, direction))


def test_a1_partial_assignment_keeps_unassigned_singleton(monkeypatch) -> None:
    boxes = (PanelBox(0, 0, 100, 200), PanelBox(200, 0, 300, 200))
    monkeypatch.setattr(
        v2,
        "segment_panel_groups",
        lambda pixels: PanelSegmentation(boxes, True, "reliable"),
    )
    refs = (
        ref(0, (20, 20, 40, 60)),
        ref(1, (220, 20, 240, 60)),
        ref(2, (400, 20, 430, 60)),
    )
    result = v2.run_reading_order_v2_arm(
        np.zeros((200, 500, 3), dtype=np.uint8),
        refs,
        page_height=200,
        arm=ReadingOrderArm.A1_B0_PANEL_ONLY,
        page_id="fixture",
        repository_sha="0" * 40,
    )
    region = result.diagnostic.regions[2]
    assert result.diagnostic.used_panel_evidence is True
    assert result.diagnostic.panel_evidence_mode.value == "partial"
    assert region.assignment_status.value == "unassigned"
    assert region.assigned_group_id is None
    assert {id(item) for item in result.regions} == {id(item.region) for item in refs}


def test_a1_ambiguous_singleton_is_not_coerced(monkeypatch) -> None:
    boxes = (PanelBox(0, 0, 150, 200), PanelBox(100, 0, 250, 200))
    monkeypatch.setattr(
        v2,
        "segment_panel_groups",
        lambda pixels: PanelSegmentation(boxes, True, "reliable"),
    )
    refs = (
        ref(0, (20, 20, 40, 50)),
        ref(1, (200, 20, 220, 50)),
        ref(2, (120, 20, 130, 50)),
    )
    result = v2.run_reading_order_v2_arm(
        np.zeros((200, 300, 3), dtype=np.uint8),
        refs,
        page_height=200,
        arm=ReadingOrderArm.A1_B0_PANEL_ONLY,
        page_id="fixture",
        repository_sha="0" * 40,
    )
    diagnostic = result.diagnostic.regions[2]
    assert diagnostic.assignment_status.value == "ambiguous"
    assert diagnostic.ambiguity_count == 2
    assert diagnostic.assigned_group_id is None


def test_a1_insufficient_confident_groups_uses_exact_page_fallback(monkeypatch) -> None:
    boxes = (PanelBox(0, 0, 100, 200), PanelBox(200, 0, 300, 200))
    monkeypatch.setattr(
        v2,
        "segment_panel_groups",
        lambda pixels: PanelSegmentation(boxes, True, "reliable"),
    )
    refs = (ref(0, (20, 20, 40, 60)), ref(1, (400, 20, 430, 60)))
    result = v2.run_reading_order_v2_arm(
        np.zeros((200, 500, 3), dtype=np.uint8),
        refs,
        page_height=200,
        arm=ReadingOrderArm.A1_B0_PANEL_ONLY,
        page_id="fixture",
        repository_sha="0" * 40,
    )
    assert result.diagnostic.used_panel_evidence is False
    assert result.diagnostic.fallback_reason == "insufficient-confident-panel-groups"
    assert result.diagnostic.final_order == result.diagnostic.fallback_order


def test_a1_segmentation_failure_uses_exact_page_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        v2,
        "segment_panel_groups",
        lambda pixels: PanelSegmentation((), False, "no-panel-candidates"),
    )
    refs = (ref(0, (20, 20, 40, 60)), ref(1, (80, 20, 100, 60)))
    result = v2.run_reading_order_v2_arm(
        np.zeros((200, 200, 3), dtype=np.uint8),
        refs,
        page_height=200,
        arm=ReadingOrderArm.A1_B0_PANEL_ONLY,
        page_id="fixture",
        repository_sha="0" * 40,
    )
    assert result.diagnostic.fallback_reason == "no-panel-candidates"
    assert result.diagnostic.final_order == result.diagnostic.fallback_order


def test_b1_horizontal_vertical_mixed_and_ambiguous_order() -> None:
    horizontal = (
        ref(0, (200, 10, 240, 60), "h"),
        ref(1, (50, 20, 90, 70), "hr"),
    )
    ordered_h, _ = v2._b1_local_order(horizontal, page_height=300, tier_prefix="x-")
    assert [item.region_id for item in ordered_h] == ["r1", "r0"]

    vertical = (
        ref(0, (200, 10, 240, 60), "v"),
        ref(1, (50, 20, 90, 70), "vr"),
    )
    ordered_v, _ = v2._b1_local_order(vertical, page_height=300, tier_prefix="x-")
    assert [item.region_id for item in ordered_v] == ["r0", "r1"]

    mixed_higher_h = (
        ref(0, (50, 10, 90, 70), "h"),
        ref(1, (200, 20, 240, 80), "v"),
    )
    ordered_mh, _ = v2._b1_local_order(
        mixed_higher_h, page_height=300, tier_prefix="x-"
    )
    assert [item.region_id for item in ordered_mh] == ["r0", "r1"]

    mixed_higher_v = (
        ref(0, (50, 30, 90, 90), "h"),
        ref(1, (200, 10, 240, 70), "v"),
    )
    ordered_mv, _ = v2._b1_local_order(
        mixed_higher_v, page_height=300, tier_prefix="x-"
    )
    assert [item.region_id for item in ordered_mv] == ["r1", "r0"]

    ambiguous = (
        ref(0, (100, 10, 140, 60), "x"),
        ref(1, (100, 10, 140, 60), "x"),
    )
    ordered_a, traces = v2._b1_local_order(
        ambiguous, page_height=300, tier_prefix="x-"
    )
    assert [item.region_id for item in ordered_a] == ["r0", "r1"]
    assert len({trace.run_id for trace in traces.values()}) == 2
