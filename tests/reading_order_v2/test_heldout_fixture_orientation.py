from __future__ import annotations

import json
from pathlib import Path

from scripts.reading_order_v2.contracts import PAGE_IDS
from scripts.reading_order_v2.fixtures import load_textblock_regions

from mangasensei.ocr.reading_order import _partition_manga_tiers  # noqa: PLC2701

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


DESIGNATED_B_PAIR_STRUCTURE = {
    "horizontal": (
        ("H01", "ro2h-H01-q001"),
        ("H01", "ro2h-H01-q002"),
        ("H03", "ro2h-H03-q001"),
        ("H04", "ro2h-H04-q003"),
    ),
    "mixed": (
        ("H03", "ro2h-H03-q002"),
        ("H04", "ro2h-H04-q002"),
    ),
    "vertical": (
        ("H02", "ro2h-H02-q001"),
        ("H02", "ro2h-H02-q002"),
        ("H03", "ro2h-H03-q003"),
        ("H04", "ro2h-H04-q001"),
    ),
}


def _pair_by_id(annotation: dict[str, object], pair_id: str) -> dict[str, object]:
    pairs = annotation["qualificationPairs"]
    assert isinstance(pairs, list)
    for pair in pairs:
        assert isinstance(pair, dict)
        if pair["id"] == pair_id:
            return pair
    raise AssertionError(f"missing qualification pair: {pair_id}")


def _tier_index_by_region(
    regions: tuple[object, ...],
    annotation: dict[str, object],
    panel_id: str,
) -> dict[str, int]:
    expectations = annotation["assignmentExpectations"]
    assert isinstance(expectations, list)
    panel_region_ids = {
        str(item["regionId"])
        for item in expectations
        if isinstance(item, dict)
        and item["expected"] == "unique"
        and item["panelId"] == panel_id
    }
    refs = [region for region in regions if region.region_id in panel_region_ids]
    tiers = _partition_manga_tiers(
        [region.region for region in refs],
        page_height=2048,
    )
    result: dict[str, int] = {}
    ref_by_object = {id(region.region): region.region_id for region in refs}
    for tier_index, tier in enumerate(tiers):
        for item in tier:
            result[ref_by_object[id(item.region)]] = tier_index
    return result


def _unique_panel_id(annotation: dict[str, object], region_id: str) -> str:
    expectations = annotation["assignmentExpectations"]
    assert isinstance(expectations, list)
    matches = [
        item
        for item in expectations
        if isinstance(item, dict) and item["regionId"] == region_id
    ]
    assert len(matches) == 1
    item = matches[0]
    assert item["expected"] == "unique"
    assert isinstance(item["panelId"], str)
    return item["panelId"]


def test_designated_b_pairs_have_static_production_tier_prerequisites() -> None:
    for relationship, designated in DESIGNATED_B_PAIR_STRUCTURE.items():
        for page_id, pair_id in designated:
            _, regions = load_textblock_regions(CORPUS_ROOT / "inputs" / f"{page_id}.json")
            annotation = json.loads(
                (CORPUS_ROOT / "annotations" / f"{page_id}.json").read_text(encoding="utf-8")
            )
            pair = _pair_by_id(annotation, pair_id)
            earlier_id = str(pair["earlier"])
            later_id = str(pair["later"])
            by_id = {region.region_id: region for region in regions}
            earlier = by_id[earlier_id]
            later = by_id[later_id]
            earlier_panel = _unique_panel_id(annotation, earlier_id)
            later_panel = _unique_panel_id(annotation, later_id)
            assert earlier_panel == later_panel
            tier_index = _tier_index_by_region(regions, annotation, earlier_panel)
            assert tier_index[earlier_id] == tier_index[later_id]

            earlier_orientation = _normalized_direction(earlier.region.direction)
            later_orientation = _normalized_direction(later.region.direction)
            earlier_x = float(earlier.region.center[0])
            later_x = float(later.region.center[0])
            earlier_y = float(earlier.region.xyxy[1])
            later_y = float(later.region.xyxy[1])

            if relationship == "horizontal":
                assert earlier_orientation == later_orientation == "horizontal"
                assert earlier_x < later_x
                assert "horizontal-only" in pair["slices"]
            elif relationship == "mixed":
                assert {earlier_orientation, later_orientation} == {"horizontal", "vertical"}
                assert "mixed" in pair["slices"]
                if page_id == "H03":
                    horizontal_y = earlier_y if earlier_orientation == "horizontal" else later_y
                    vertical_y = earlier_y if earlier_orientation == "vertical" else later_y
                    assert horizontal_y < vertical_y
                elif page_id == "H04":
                    horizontal_y = earlier_y if earlier_orientation == "horizontal" else later_y
                    vertical_y = earlier_y if earlier_orientation == "vertical" else later_y
                    assert vertical_y < horizontal_y
            elif relationship == "vertical":
                assert earlier_orientation == later_orientation == "vertical"
                assert earlier_x > later_x
                assert "vertical-only" in pair["slices"]
            else:
                raise AssertionError(f"unknown B relationship: {relationship}")
