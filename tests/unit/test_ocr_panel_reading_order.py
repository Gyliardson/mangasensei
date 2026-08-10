from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import pytest

from mangasensei.ocr.reading_order import (
    PanelBox,
    assign_regions_to_groups,
    manga_tier_order,
    panel_aware_reading_order,
    panel_precedence_order,
    segment_panel_groups,
)


@dataclass(slots=True)
class GeometryRegion:
    name: str
    xyxy: tuple[int, int, int, int]
    orientation: str = "vertical"
    text: str = "fixture"
    prob: float = 0.9
    angle: float = 0.0


def _page() -> np.ndarray:
    return np.full((1000, 1000, 3), 255, dtype=np.uint8)


def _frame(
    image: np.ndarray,
    box: PanelBox,
    *,
    gap: tuple[str, int, int] | None = None,
) -> None:
    cv2.rectangle(image, (box.x1, box.y1), (box.x2, box.y2), (0, 0, 0), 6)
    if gap is None:
        return
    side, start, end = gap
    if side == "right":
        cv2.line(
            image,
            (box.x2, max(box.y1, start)),
            (box.x2, min(box.y2, end)),
            (255, 255, 255),
            8,
        )
    elif side == "top":
        cv2.line(
            image,
            (max(box.x1, start), box.y1),
            (min(box.x2, end), box.y1),
            (255, 255, 255),
            8,
        )
    else:
        raise AssertionError(side)


def _names(regions: object) -> list[str]:
    return [region.name for region in regions]  # type: ignore[attr-defined]


def test_panel_segmentation_and_assignment_are_separate_deterministic_stages() -> None:
    image = _page()
    _frame(image, PanelBox(520, 50, 950, 480))
    _frame(image, PanelBox(50, 50, 480, 480))
    segmentation = segment_panel_groups(image)

    assert segmentation.reliable
    assert len(segmentation.boxes) == 2

    regions = [
        GeometryRegion("left", (130, 100, 230, 260)),
        GeometryRegion("right", (740, 280, 840, 440)),
    ]
    assignment = assign_regions_to_groups(segmentation.boxes, regions)

    assert assignment.reliable
    assert sorted(index for group in assignment.groups for index in group) == [0, 1]


def test_panel_assignment_keeps_empty_groups_without_dropping_ocr_regions() -> None:
    boxes = (
        PanelBox(520, 50, 950, 420),
        PanelBox(50, 50, 480, 420),
        PanelBox(50, 460, 950, 920),
    )
    regions = [
        GeometryRegion("upper-left", (130, 100, 230, 260)),
        GeometryRegion("lower", (760, 550, 850, 760)),
    ]

    assignment = assign_regions_to_groups(boxes, regions)

    assert assignment.reliable
    assert len(assignment.groups) == 3
    assert any(group == () for group in assignment.groups)
    assert sorted(index for group in assignment.groups for index in group) == [0, 1]


def test_precedence_graph_beats_row_heuristic_failure_shape() -> None:
    boxes = (
        PanelBox(50, 50, 450, 550),
        PanelBox(500, 250, 950, 700),
    )

    assert panel_precedence_order(boxes, (0, 1)) == (1, 0)


@pytest.mark.parametrize(
    ("name", "left_box", "right_box", "left_region", "right_region"),
    [
        (
            "offset-text-starts",
            PanelBox(50, 50, 480, 480),
            PanelBox(520, 50, 950, 480),
            (130, 100, 230, 260),
            (740, 280, 840, 440),
        ),
        (
            "substantially-lower-right-text",
            PanelBox(50, 50, 480, 500),
            PanelBox(520, 50, 950, 500),
            (120, 80, 220, 220),
            (760, 350, 850, 480),
        ),
        (
            "unequal-height-panels",
            PanelBox(50, 50, 480, 430),
            PanelBox(520, 50, 950, 700),
            (140, 100, 240, 260),
            (760, 330, 850, 540),
        ),
    ],
)
def test_offset_neighboring_panels_follow_panel_flow(
    name: str,
    left_box: PanelBox,
    right_box: PanelBox,
    left_region: tuple[int, int, int, int],
    right_region: tuple[int, int, int, int],
) -> None:
    del name
    image = _page()
    _frame(image, right_box)
    _frame(image, left_box)
    regions = [
        GeometryRegion("left", left_region),
        GeometryRegion("right", right_region),
    ]

    assert _names(manga_tier_order(regions, page_height=1000)) == ["left", "right"]

    result = panel_aware_reading_order(image, regions, page_height=1000)

    assert result.used_panel_evidence
    assert result.fallback_reason is None
    assert _names(result.regions) == ["right", "left"]


def test_mixed_text_orientation_does_not_override_panel_flow() -> None:
    image = _page()
    _frame(image, PanelBox(520, 50, 950, 480))
    _frame(image, PanelBox(50, 50, 480, 480))
    regions = [
        GeometryRegion("left-horizontal", (100, 100, 320, 180), "horizontal"),
        GeometryRegion("right-vertical", (760, 300, 850, 450), "vertical"),
    ]

    result = panel_aware_reading_order(image, regions, page_height=1000)

    assert result.used_panel_evidence
    assert _names(result.regions) == ["right-vertical", "left-horizontal"]


def test_panel_with_no_ocr_text_does_not_break_neighboring_panel_order() -> None:
    image = _page()
    _frame(image, PanelBox(520, 50, 950, 420))
    _frame(image, PanelBox(50, 50, 480, 420))
    _frame(image, PanelBox(50, 460, 950, 920))
    regions = [
        GeometryRegion("upper-left", (130, 100, 230, 260)),
        GeometryRegion("lower", (760, 550, 850, 760)),
    ]

    result = panel_aware_reading_order(image, regions, page_height=1000)

    assert result.used_panel_evidence
    assert result.panel_count == 3
    assert _names(result.regions) == ["upper-left", "lower"]


def test_open_panel_borders_can_still_supply_deterministic_panel_evidence() -> None:
    image = _page()
    _frame(image, PanelBox(520, 50, 950, 480), gap=("right", 180, 280))
    _frame(image, PanelBox(50, 50, 480, 480), gap=("top", 170, 300))
    regions = [
        GeometryRegion("left", (120, 100, 220, 260)),
        GeometryRegion("right", (760, 300, 850, 450)),
    ]

    result = panel_aware_reading_order(image, regions, page_height=1000)

    assert result.used_panel_evidence
    assert _names(result.regions) == ["right", "left"]


@pytest.mark.parametrize("case", ["inset", "overlap", "no-frame"])
def test_ambiguous_or_missing_panel_evidence_uses_exact_text_fallback(case: str) -> None:
    image = _page()
    if case == "inset":
        _frame(image, PanelBox(70, 60, 930, 920))
        _frame(image, PanelBox(610, 120, 880, 390))
        regions = [
            GeometryRegion("outer", (150, 180, 260, 360)),
            GeometryRegion("inset", (690, 180, 800, 330)),
        ]
    elif case == "overlap":
        _frame(image, PanelBox(100, 100, 650, 650))
        _frame(image, PanelBox(450, 250, 900, 800))
        regions = [
            GeometryRegion("first", (200, 180, 300, 340)),
            GeometryRegion("second", (650, 400, 760, 560)),
        ]
    else:
        regions = [
            GeometryRegion("upper-left", (120, 100, 220, 260)),
            GeometryRegion("lower-right", (760, 600, 850, 800)),
        ]

    fallback = manga_tier_order(regions, page_height=1000)
    result = panel_aware_reading_order(image, regions, page_height=1000)

    assert not result.used_panel_evidence
    assert result.fallback_reason is not None
    assert list(result.regions) == fallback


def test_existing_top_bottom_and_same_row_fallback_contracts_remain_unchanged() -> None:
    image = _page()
    top_bottom = [
        GeometryRegion("lower-right", (760, 620, 850, 800)),
        GeometryRegion("upper-left", (120, 130, 220, 300)),
        GeometryRegion("upper-right", (760, 100, 850, 280)),
    ]
    same_row = [
        GeometryRegion("left", (120, 100, 220, 260)),
        GeometryRegion("right", (760, 110, 850, 270)),
    ]

    first = panel_aware_reading_order(image, top_bottom, page_height=1000)
    second = panel_aware_reading_order(image, same_row, page_height=1000)

    assert not first.used_panel_evidence
    assert _names(first.regions) == ["upper-right", "upper-left", "lower-right"]
    assert not second.used_panel_evidence
    assert _names(second.regions) == ["right", "left"]


def test_identical_input_is_deterministic_and_preserves_region_payloads() -> None:
    image = _page()
    _frame(image, PanelBox(520, 50, 950, 480))
    _frame(image, PanelBox(50, 50, 480, 480))
    regions = [
        GeometryRegion("left", (130, 100, 230, 260), text="left-text", prob=0.81),
        GeometryRegion("right", (740, 280, 840, 440), text="right-text", prob=0.93),
    ]
    before = {
        id(region): (region.text, region.prob, region.xyxy, region.angle)
        for region in regions
    }

    first = panel_aware_reading_order(image, regions, page_height=1000)
    second = panel_aware_reading_order(image.copy(), list(regions), page_height=1000)

    assert _names(first.regions) == _names(second.regions) == ["right", "left"]
    assert first.used_panel_evidence == second.used_panel_evidence
    assert first.fallback_reason == second.fallback_reason
    assert first.panel_count == second.panel_count
    assert len(first.regions) == len(regions)
    assert {id(region) for region in first.regions} == {id(region) for region in regions}
    assert {
        id(region): (region.text, region.prob, region.xyxy, region.angle)
        for region in first.regions
    } == before
