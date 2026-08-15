from __future__ import annotations

from scripts.reading_order_v2.fixtures import FIXTURE_PROBABILITY, FIXTURE_TEXT, build_region


def make(region_id: str, source_index: int, line: list[list[int]]) -> dict[str, object]:
    return {
        "regionId": region_id,
        "sourceIndex": source_index,
        "lines": [line],
        "angle": 0,
    }


def test_actual_textblock_horizontal_geometry_and_identity() -> None:
    region = build_region(
        make("h", 0, [[10, 10], [110, 10], [110, 40], [10, 40]])
    )
    block = region.region
    assert region.region_id == "h"
    assert region.source_index == 0
    assert tuple(int(value) for value in block.xyxy) == (10, 10, 110, 40)
    assert block.direction == "h"
    assert block.text == FIXTURE_TEXT
    assert block.prob == FIXTURE_PROBABILITY
    assert block.target_lang == ""
    min_rect, _ = block.min_rect
    assert min_rect.shape == (4, 2)


def test_actual_textblock_vertical_geometry() -> None:
    block = build_region(
        make("v", 1, [[20, 10], [50, 10], [50, 130], [20, 130]])
    ).region
    assert tuple(int(value) for value in block.xyxy) == (20, 10, 50, 130)
    assert block.direction == "v"


def test_multiline_geometry_expands_real_textblock_xyxy() -> None:
    value = {
        "regionId": "m",
        "sourceIndex": 0,
        "angle": 0,
        "lines": [
            [[10, 10], [40, 10], [40, 100], [10, 100]],
            [[50, 20], [80, 20], [80, 120], [50, 120]],
        ],
    }
    block = build_region(value).region
    assert tuple(int(item) for item in block.xyxy) == (10, 10, 80, 120)


def test_structural_input_rejects_semantic_text_or_probability_channel() -> None:
    value = make("x", 0, [[0, 0], [50, 0], [50, 20], [0, 20]])
    value["text"] = "semantic"
    try:
        build_region(value)
    except ValueError as exc:
        assert "structural input keys" in str(exc)
    else:
        raise AssertionError("semantic input key was accepted")
