from __future__ import annotations

import json

from scripts.reading_order_v2.fixtures import load_textblock_regions


def test_real_textblock_fixture_geometry_and_direction(tmp_path) -> None:
    path = tmp_path / "H01.json"
    payload = {
        "schemaVersion": "reading-order-v2-input-v1",
        "pageId": "H01",
        "width": 1440,
        "height": 2048,
        "regions": [
            {
                "regionId": "h",
                "sourceIndex": 0,
                "angle": 0,
                "lines": [[[100, 100], [300, 100], [300, 150], [100, 150]]],
            },
            {
                "regionId": "v",
                "sourceIndex": 1,
                "angle": 0,
                "lines": [[[500, 100], [550, 100], [550, 350], [500, 350]]],
            },
            {
                "regionId": "multi",
                "sourceIndex": 2,
                "angle": 0,
                "lines": [
                    [[700, 100], [760, 100], [760, 250], [700, 250]],
                    [[620, 110], [680, 110], [680, 260], [620, 260]],
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    page, refs = load_textblock_regions(path)
    assert page.page_id == "H01"
    assert refs[0].region.direction == "h"
    assert refs[1].region.direction == "v"
    assert tuple(int(value) for value in refs[2].region.xyxy) == (620, 100, 760, 260)
    assert refs[0].region.text == refs[1].region.text == refs[2].region.text == "fixture"
    assert refs[0].region.prob == refs[1].region.prob == refs[2].region.prob == 0.5
    assert refs[0].region.min_rect.shape == (1, 4, 2)
    assert [ref.region_id for ref in refs] == ["h", "v", "multi"]
