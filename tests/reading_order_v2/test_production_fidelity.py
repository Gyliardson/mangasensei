from __future__ import annotations

import numpy as np

from mangasensei.ocr.diagnostics.reading_order_v2 import run_reading_order_v2_arm
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ExperimentRegion, ReadingOrderArm
from mangasensei.ocr.reading_order import (
    PanelBox,
    PanelSegmentation,
    _candidate_group_indices,
    _manga_tier_items,
    _partition_manga_tiers,
    assign_regions_to_groups,
    manga_tier_order,
    panel_aware_reading_order,
)


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


def _refs(regions: list[Region]) -> tuple[ExperimentRegion, ...]:
    return tuple(
        ExperimentRegion(f"r{index:02d}", index, region)
        for index, region in enumerate(regions)
    )


def test_partition_helper_preserves_exact_current_tier_order() -> None:
    regions = [
        Region((900, 100, 1000, 180)),
        Region((100, 120, 200, 200)),
        Region((700, 400, 800, 470)),
    ]
    tiers = _partition_manga_tiers(regions, page_height=1000)
    ordered_items = _manga_tier_items(regions, page_height=1000)
    assert [[item.region for item in tier] for tier in tiers] == [
        [regions[0], regions[1]],
        [regions[2]],
    ]
    assert [item.region for item in ordered_items] == manga_tier_order(
        regions, page_height=1000
    )


def test_candidate_membership_is_exact_strict_center_containment() -> None:
    boxes = (PanelBox(0, 0, 100, 100), PanelBox(100, 0, 200, 100))
    assert _candidate_group_indices(boxes, Region((90, 40, 110, 60))) == (0, 1)
    assert _candidate_group_indices(boxes, Region((120, 40, 140, 60))) == (1,)
    assert _candidate_group_indices(boxes, Region((210, 40, 220, 60))) == ()


def test_assign_regions_keeps_current_early_failure_semantics() -> None:
    boxes = (PanelBox(0, 0, 100, 100), PanelBox(100, 0, 200, 100))
    regions = [
        Region((120, 40, 140, 60)),
        Region((210, 40, 220, 60)),
        Region((20, 40, 40, 60)),
    ]
    result = assign_regions_to_groups(boxes, regions)
    assert result.reliable is False
    assert result.reason == "region-unassigned-or-ambiguous"
    assert result.groups == ((), (0,))


def test_a0_control_direct_production_equivalence_on_real_panel_segmentation() -> None:
    pixels = np.full((500, 1000, 3), 255, dtype=np.uint8)
    pixels[50:450, 500:505] = 0
    pixels[50:450, 950:955] = 0
    pixels[50:55, 500:955] = 0
    pixels[445:450, 500:955] = 0
    pixels[50:450, 50:55] = 0
    pixels[50:450, 450:455] = 0
    pixels[50:55, 50:455] = 0
    pixels[445:450, 50:455] = 0
    regions = [Region((700, 100, 800, 180)), Region((200, 100, 300, 180))]
    segmentation = PanelSegmentation(
        (PanelBox(50, 50, 455, 450), PanelBox(500, 50, 955, 450)),
        True,
        "reliable",
    )
    direct = panel_aware_reading_order(pixels, regions, page_height=500)
    assert direct.used_panel_evidence is True
    assert direct.regions == (regions[0], regions[1])

    experimental = run_reading_order_v2_arm(
        pixels,
        _refs(regions),
        page_height=500,
        arm=ReadingOrderArm.A0_B0_CONTROL,
        page_id="fixture",
        repository_sha="0" * 40,
    )
    assert experimental.regions == direct.regions
    assert experimental.diagnostic.used_panel_evidence == direct.used_panel_evidence
    assert experimental.diagnostic.fallback_reason == direct.fallback_reason
    assert experimental.diagnostic.segmentation.detected_group_count == direct.panel_count
    assert segmentation.reason == "reliable"


def test_a0_control_direct_production_equivalence_on_fewer_than_two_regions() -> None:
    pixels = np.zeros((200, 200, 3), dtype=np.uint8)
    regions = [Region((10, 20, 40, 60))]
    direct = panel_aware_reading_order(pixels, regions, page_height=200)
    experimental = run_reading_order_v2_arm(
        pixels,
        _refs(regions),
        page_height=200,
        arm=ReadingOrderArm.A0_B0_CONTROL,
        page_id="fixture",
        repository_sha="0" * 40,
    )
    assert experimental.regions == direct.regions
    assert experimental.diagnostic.fallback_reason == direct.fallback_reason
    assert experimental.diagnostic.used_panel_evidence == direct.used_panel_evidence
