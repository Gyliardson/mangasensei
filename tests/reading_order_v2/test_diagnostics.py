from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

import mangasensei.ocr.diagnostics.reading_order_v2 as v2
import mangasensei.ocr.reading_order as production
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import (
    ArmId,
    ExperimentRegion,
    diagnostic_to_dict,
)
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation


@dataclass(slots=True)
class Region:
    xyxy: tuple[int, int, int, int]
    direction: str = "v"

    @property
    def min_rect(self) -> np.ndarray:
        x1, y1, x2, y2 = self.xyxy
        return np.array(
            [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]], dtype=np.int64
        )


def _run_with_segmentation(
    monkeypatch: pytest.MonkeyPatch,
    segmentation: PanelSegmentation,
    arm: ArmId,
):
    monkeypatch.setattr(production, "segment_panel_groups", lambda pixels: segmentation)
    monkeypatch.setattr(v2, "segment_panel_groups", lambda pixels: segmentation)
    raw = [Region((130, 100, 230, 260)), Region((740, 280, 840, 440))]
    refs = tuple(ExperimentRegion(f"r{i}", i, region) for i, region in enumerate(raw))
    result = v2.run_reading_order_v2_arm(
        np.zeros((1000, 1000, 3), dtype=np.uint8),
        refs,
        page_height=1000,
        repository_sha="a" * 40,
        page_id="unit",
        arm_id=arm,
    )
    return diagnostic_to_dict(result.diagnostic)


def test_diagnostic_records_actual_memberships_and_precedence_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(520, 50, 950, 480), PanelBox(50, 50, 480, 480))
    payload = _run_with_segmentation(
        monkeypatch,
        PanelSegmentation(boxes, True, "reliable"),
        ArmId.A0_B0_CONTROL,
    )
    assert payload["schemaVersion"] == "reading-order-v2-diagnostic-v1"
    assert [group["groupId"] for group in payload["groups"]] == ["g000", "g001"]
    assert payload["regions"][0]["candidateGroupIds"] == ["g001"]
    edges = [edge for group in payload["groups"] for edge in group["precedenceEdges"]]
    assert edges == [
        {
            "targetGroupId": "g001",
            "rule": "same-level-right-before-left",
            "xOverlap": {"numerator": 0, "denominator": 430},
            "yOverlap": {"numerator": 430, "denominator": 430},
        }
    ]
    assert all("polygon" in group and group["polygon"] is None for group in payload["groups"])
    assert all(isinstance(region["center2x"], int) for region in payload["regions"])


def test_group_tie_key_matches_actual_a0_and_a1_scheduler_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(520, 50, 950, 480), PanelBox(50, 50, 480, 480))
    segmentation = PanelSegmentation(boxes, True, "reliable")
    captured: dict[str, list[tuple[Any, ...]]] = {}

    original_production_scheduler = production._deterministic_topological_order

    def capture_a0(
        node_count: int,
        edges: Sequence[tuple[int, int]],
        *,
        tie_key: Callable[[int], tuple[Any, ...]],
    ) -> tuple[int, ...] | None:
        captured["a0"] = [tie_key(index) for index in range(node_count)]
        return original_production_scheduler(node_count, edges, tie_key=tie_key)

    monkeypatch.setattr(production, "_deterministic_topological_order", capture_a0)
    a0 = _run_with_segmentation(monkeypatch, segmentation, ArmId.A0_B0_CONTROL)
    assert [group["tieKey"] for group in a0["groups"]] == [
        list(key) for key in captured["a0"]
    ]

    original_a1_scheduler = v2._deterministic_topological_order

    def capture_a1(
        node_count: int,
        edges: Sequence[tuple[int, int]],
        *,
        tie_key: Callable[[int], tuple[Any, ...]],
    ) -> tuple[int, ...] | None:
        captured["a1"] = [tie_key(index) for index in range(node_count)]
        return original_a1_scheduler(node_count, edges, tie_key=tie_key)

    monkeypatch.setattr(v2, "_deterministic_topological_order", capture_a1)
    a1 = _run_with_segmentation(monkeypatch, segmentation, ArmId.A1_B0_PANEL_ONLY)
    assert [group["tieKey"] for group in a1["groups"]] == [
        list(key) for key in captured["a1"]
    ]


def test_unreliable_segmentation_keeps_candidate_membership_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes = (PanelBox(520, 50, 950, 480), PanelBox(50, 50, 480, 480))
    payload = _run_with_segmentation(
        monkeypatch,
        PanelSegmentation(boxes, False, "ambiguous-overlap"),
        ArmId.A1_B0_PANEL_ONLY,
    )
    assert payload["segmentation"]["reliable"] is False
    assert payload["usedPanelEvidence"] is False
    assert payload["regions"][0]["candidateGroupIds"] == ["g001"]
    assert payload["regions"][1]["candidateGroupIds"] == ["g000"]
    assert all(region["assignmentReason"] == "not-attempted" for region in payload["regions"])
    assert payload["groups"][0]["candidateRegionIds"] == ["r1"]
    assert payload["groups"][1]["candidateRegionIds"] == ["r0"]
    assert all(not group["confidentRegionIds"] for group in payload["groups"])
    assert all(group["tieKey"] == [] for group in payload["groups"])
