from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from mangasensei.ocr.diagnostics.reading_order_v2 import run_reading_order_v2_arm
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId, ExperimentRegion
from mangasensei.ocr.reading_order import PanelBox, panel_aware_reading_order


@dataclass(slots=True)
class Region:
    name: str
    xyxy: tuple[int, int, int, int]
    direction: str = "v"
    text: str = "fixture"
    prob: float = 0.5
    angle: float = 0.0

    @property
    def min_rect(self) -> np.ndarray:
        x1, y1, x2, y2 = self.xyxy
        return np.array([[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]], dtype=np.int64)


def _frame(image: np.ndarray, box: PanelBox) -> None:
    cv2.rectangle(image, (box.x1, box.y1), (box.x2, box.y2), (0, 0, 0), 6)


def _refs(regions: list[Region]) -> tuple[ExperimentRegion, ...]:
    return tuple(
        ExperimentRegion(f"r{index}", index, region)
        for index, region in enumerate(regions)
    )


def test_a0_b0_is_direct_production_order_for_panel_success() -> None:
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    _frame(image, PanelBox(520, 50, 950, 480))
    _frame(image, PanelBox(50, 50, 480, 480))
    regions = [Region("left", (130, 100, 230, 260)), Region("right", (740, 280, 840, 440))]
    direct = panel_aware_reading_order(image, regions, page_height=1000)
    experimental = run_reading_order_v2_arm(
        image,
        _refs(regions),
        page_height=1000,
        repository_sha="2" * 40,
        page_id="unit",
        arm_id=ArmId.A0_B0_CONTROL,
    )
    assert [item.region for item in experimental.ordered_regions] == list(direct.regions)
    assert experimental.diagnostic.used_panel_evidence == direct.used_panel_evidence
    assert experimental.diagnostic.fallback_reason == direct.fallback_reason
    assert len(experimental.diagnostic.groups) == direct.panel_count


def test_a0_b0_is_direct_production_order_for_page_fallback() -> None:
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    regions = [Region("left", (120, 100, 220, 260)), Region("right", (760, 110, 850, 270))]
    direct = panel_aware_reading_order(image, regions, page_height=1000)
    experimental = run_reading_order_v2_arm(
        image,
        _refs(regions),
        page_height=1000,
        repository_sha="2" * 40,
        page_id="unit",
        arm_id=ArmId.A0_B0_CONTROL,
    )
    assert [item.region for item in experimental.ordered_regions] == list(direct.regions)
    assert not experimental.diagnostic.used_panel_evidence
    assert experimental.diagnostic.fallback_reason == direct.fallback_reason


def test_a0_b0_fewer_than_two_regions_is_direct_production_equivalent() -> None:
    image = np.full((1000, 1000, 3), 255, dtype=np.uint8)
    regions = [Region("only", (100, 100, 200, 300))]
    direct = panel_aware_reading_order(image, regions, page_height=1000)
    experimental = run_reading_order_v2_arm(
        image, _refs(regions), page_height=1000, repository_sha="2" * 40,
        page_id="unit", arm_id=ArmId.A0_B0_CONTROL,
    )
    assert [item.region for item in experimental.ordered_regions] == list(direct.regions)
    assert experimental.diagnostic.fallback_reason == "fewer-than-two-regions"
