from __future__ import annotations

import json
from pathlib import Path

from scripts.reading_order_v2.contracts import PAGE_IDS
from scripts.reading_order_v2.fixtures import load_textblock_regions

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "assets" / "reading-order-v2" / "heldout-v1"


def _normalized_direction(direction: str) -> str:
    if direction in {"h", "hr"}:
        return "horizontal"
    if direction in {"v", "vr"}:
        return "vertical"
    return "ambiguous"


def _memberships(
    center_x: float, center_y: float, panels: list[dict[str, object]]
) -> list[str]:
    matches: list[str] = []
    for panel in panels:
        bbox = panel["bbox"]
        assert isinstance(bbox, dict)
        x = int(bbox["x"])
        y = int(bbox["y"])
        width = int(bbox["width"])
        height = int(bbox["height"])
        if x <= center_x <= x + width and y <= center_y <= y + height:
            matches.append(str(panel["id"]))
    return matches


def test_real_textblock_fixture_orientation_matches_authored_expectations() -> None:
    for page_id in PAGE_IDS:
        _, regions = load_textblock_regions(CORPUS_ROOT / "inputs" / f"{page_id}.json")
        annotation = json.loads(
            (CORPUS_ROOT / "annotations" / f"{page_id}.json").read_text(encoding="utf-8")
        )
        expected = {
            item["regionId"]: item["expected"]
            for item in annotation["orientationExpectations"]
        }
        assert set(expected) == {region.region_id for region in regions}
        for region in regions:
            assert _normalized_direction(region.region.direction) == expected[region.region_id]


def test_assignment_expectations_match_authored_panel_geometry() -> None:
    for page_id in PAGE_IDS:
        _, regions = load_textblock_regions(CORPUS_ROOT / "inputs" / f"{page_id}.json")
        annotation = json.loads(
            (CORPUS_ROOT / "annotations" / f"{page_id}.json").read_text(encoding="utf-8")
        )
        panels = annotation["panels"]
        expectations = {
            item["regionId"]: item for item in annotation["assignmentExpectations"]
        }
        assert set(expectations) == {region.region_id for region in regions}
        for region in regions:
            item = expectations[region.region_id]
            if item["expected"] == "not-applicable":
                assert not panels
                assert item["panelId"] is None
                continue
            center_x, center_y = (float(value) for value in region.region.center)
            matches = _memberships(center_x, center_y, panels)
            if item["expected"] == "unique":
                assert matches == [item["panelId"]]
            elif item["expected"] == "outside":
                assert matches == []
                assert item["panelId"] is None
            elif item["expected"] == "ambiguous":
                assert len(matches) > 1
                assert item["panelId"] is None
            else:
                raise AssertionError(f"unknown assignment expectation: {item}")
