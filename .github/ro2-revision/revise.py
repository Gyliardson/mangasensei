from __future__ import annotations

import json
from pathlib import Path

ROOT = Path.cwd()
CORPUS = ROOT / "assets" / "reading-order-v2" / "heldout-v1"


def rect(x1: int, y1: int, x2: int, y2: int) -> list[list[int]]:
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def horizontal(region_id: str, source_index: int, x1: int, x2: int, y1: int) -> dict[str, object]:
    return {
        "regionId": region_id,
        "sourceIndex": source_index,
        "lines": [rect(x1, y1, x2, y1 + 28), rect(x1, y1 + 54, x2, y1 + 82)],
        "angle": 0,
    }


def vertical(region_id: str, source_index: int, x1: int, y1: int, y2: int) -> dict[str, object]:
    return {
        "regionId": region_id,
        "sourceIndex": source_index,
        "lines": [rect(x1, y1, x1 + 28, y2), rect(x1 + 50, y1, x1 + 78, y2)],
        "angle": 0,
    }


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def svg_rect(x: int, y: int, width: int, height: int, *, fill: str = "#303030", rx: int | None = 6) -> str:
    extra = f' rx="{rx}"' if rx is not None else ""
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}"{extra} fill="{fill}"/>'


def svg_for(page_id: str, primary_h: int, control_y: int, control_h: int, glyphs: list[str]) -> str:
    lines = [
        f'<svg xmlns="http&#58;//www.w3.org/2000/svg" width="1440" height="2048" viewBox="0 0 1440 2048" data-page-id="{page_id}">',
        '<rect x="0" y="0" width="1440" height="2048" fill="#ffffff"/>',
        f'<rect x="760" y="160" width="560" height="{primary_h}" fill="#fafafa" stroke="#202020" stroke-width="12"/>',
        f'<rect x="120" y="{control_y}" width="520" height="{control_h}" fill="#fafafa" stroke="#202020" stroke-width="12"/>',
        *glyphs,
        "</svg>",
    ]
    return "\n".join(lines) + "\n"


# H01: one natural same-tier LTR horizontal sequence in the right panel.
h01_regions = [
    horizontal("ro2h-H01-r001", 0, 800, 950, 360),
    horizontal("ro2h-H01-r002", 1, 970, 1120, 366),
    horizontal("ro2h-H01-r003", 2, 250, 510, 500),
    horizontal("ro2h-H01-r004", 3, 1140, 1290, 372),
]
write_json(
    CORPUS / "inputs" / "H01.json",
    {"schemaVersion": "reading-order-v2-input-v1", "pageId": "H01", "width": 1440, "height": 2048, "regions": h01_regions},
)
h01_annotation = json.loads((CORPUS / "annotations" / "H01.json").read_text(encoding="utf-8"))
h01_annotation["readingOrder"] = ["ro2h-H01-r001", "ro2h-H01-r002", "ro2h-H01-r004", "ro2h-H01-r003"]
h01_annotation["qualificationPairs"] = [
    {"id": "ro2h-H01-q001", "earlier": "ro2h-H01-r001", "later": "ro2h-H01-r002", "slices": ["B", "clean-control", "horizontal-only"]},
    {"id": "ro2h-H01-q002", "earlier": "ro2h-H01-r002", "later": "ro2h-H01-r004", "slices": ["B", "clean-control", "horizontal-only"]},
    {"id": "ro2h-H01-q003", "earlier": "ro2h-H01-r004", "later": "ro2h-H01-r003", "slices": ["clean-control"]},
]
write_json(CORPUS / "annotations" / "H01.json", h01_annotation)
h01_glyphs = [
    svg_rect(800, 360, 150, 28), svg_rect(800, 414, 150, 28),
    svg_rect(970, 366, 150, 28), svg_rect(970, 420, 150, 28),
    svg_rect(250, 500, 260, 28), svg_rect(250, 554, 260, 28),
    svg_rect(1140, 372, 150, 28), svg_rect(1140, 426, 150, 28),
]
(CORPUS / "source" / "H01.svg").write_text(
    "\n".join([
        '<svg xmlns="http&#58;//www.w3.org/2000/svg" width="1440" height="2048" viewBox="0 0 1440 2048" data-page-id="H01">',
        '<rect x="0" y="0" width="1440" height="2048" fill="#ffffff"/>',
        '<rect x="760" y="180" width="560" height="1050" fill="#fafafa" stroke="#202020" stroke-width="12"/>',
        '<rect x="120" y="300" width="520" height="1200" fill="#fafafa" stroke="#202020" stroke-width="12"/>',
        *h01_glyphs,
        "</svg>",
    ]) + "\n",
    encoding="utf-8",
)

# H03: horizontal run begins above a vertical run, but both intentionally share one production tier.
h03_regions = [
    horizontal("ro2h-H03-r001", 0, 800, 940, 340),
    horizontal("ro2h-H03-r002", 1, 950, 1090, 350),
    vertical("ro2h-H03-r003", 2, 280, 520, 780),
    vertical("ro2h-H03-r004", 3, 1170, 400, 720),
    vertical("ro2h-H03-r005", 4, 1100, 410, 730),
]
write_json(
    CORPUS / "inputs" / "H03.json",
    {"schemaVersion": "reading-order-v2-input-v1", "pageId": "H03", "width": 1440, "height": 2048, "regions": h03_regions},
)
h03_annotation = json.loads((CORPUS / "annotations" / "H03.json").read_text(encoding="utf-8"))
h03_annotation["readingOrder"] = ["ro2h-H03-r001", "ro2h-H03-r002", "ro2h-H03-r004", "ro2h-H03-r005", "ro2h-H03-r003"]
h03_annotation["qualificationPairs"] = [
    {"id": "ro2h-H03-q001", "earlier": "ro2h-H03-r001", "later": "ro2h-H03-r002", "slices": ["B", "clean-control", "horizontal-only"]},
    {"id": "ro2h-H03-q002", "earlier": "ro2h-H03-r002", "later": "ro2h-H03-r004", "slices": ["B", "clean-control", "mixed"]},
    {"id": "ro2h-H03-q003", "earlier": "ro2h-H03-r004", "later": "ro2h-H03-r005", "slices": ["B", "clean-control", "vertical-only"]},
]
write_json(CORPUS / "annotations" / "H03.json", h03_annotation)
h03_glyphs = [
    svg_rect(800, 340, 140, 28), svg_rect(800, 394, 140, 28),
    svg_rect(950, 350, 140, 28), svg_rect(950, 404, 140, 28),
    svg_rect(280, 520, 28, 260), svg_rect(330, 520, 28, 260),
    svg_rect(1170, 400, 28, 320), svg_rect(1220, 400, 28, 320),
    svg_rect(1100, 410, 28, 320), svg_rect(1150, 410, 28, 320),
]
(CORPUS / "source" / "H03.svg").write_text(svg_for("H03", 1280, 360, 1180, h03_glyphs), encoding="utf-8")

# H04: vertical run begins above a horizontal run, both intentionally in one production tier.
h04_regions = [
    vertical("ro2h-H04-r001", 0, 1160, 300, 620),
    vertical("ro2h-H04-r002", 1, 1020, 310, 630),
    horizontal("ro2h-H04-r003", 2, 260, 510, 560),
    horizontal("ro2h-H04-r004", 3, 800, 910, 360),
    horizontal("ro2h-H04-r005", 4, 925, 1015, 370),
]
write_json(
    CORPUS / "inputs" / "H04.json",
    {"schemaVersion": "reading-order-v2-input-v1", "pageId": "H04", "width": 1440, "height": 2048, "regions": h04_regions},
)
h04_annotation = json.loads((CORPUS / "annotations" / "H04.json").read_text(encoding="utf-8"))
h04_annotation["readingOrder"] = ["ro2h-H04-r001", "ro2h-H04-r002", "ro2h-H04-r004", "ro2h-H04-r005", "ro2h-H04-r003"]
h04_annotation["qualificationPairs"] = [
    {"id": "ro2h-H04-q001", "earlier": "ro2h-H04-r001", "later": "ro2h-H04-r002", "slices": ["B", "clean-control", "vertical-only"]},
    {"id": "ro2h-H04-q002", "earlier": "ro2h-H04-r002", "later": "ro2h-H04-r004", "slices": ["B", "clean-control", "mixed"]},
    {"id": "ro2h-H04-q003", "earlier": "ro2h-H04-r004", "later": "ro2h-H04-r005", "slices": ["B", "clean-control", "horizontal-only"]},
]
write_json(CORPUS / "annotations" / "H04.json", h04_annotation)
h04_glyphs = [
    svg_rect(1160, 300, 28, 320), svg_rect(1210, 300, 28, 320),
    svg_rect(1020, 310, 28, 320), svg_rect(1070, 310, 28, 320),
    svg_rect(260, 560, 250, 28), svg_rect(260, 614, 250, 28),
    svg_rect(800, 360, 110, 28), svg_rect(800, 414, 110, 28),
    svg_rect(925, 370, 90, 28), svg_rect(925, 424, 90, 28),
]
(CORPUS / "source" / "H04.svg").write_text(svg_for("H04", 1320, 380, 1160, h04_glyphs), encoding="utf-8")

# Extend corpus-specific tests with exact production tier partition prerequisites only.
test_path = ROOT / "tests" / "reading_order_v2" / "test_heldout_fixture_orientation.py"
text = test_path.read_text(encoding="utf-8")
marker = "DESIGNATED_B_PAIR_STRUCTURE"
if marker in text:
    raise SystemExit("static B pair structure test already present")
text = text.replace(
    "from scripts.reading_order_v2.fixtures import load_textblock_regions\n",
    "from mangasensei.ocr.reading_order import _partition_manga_tiers  # noqa: PLC2701\n"
    "from scripts.reading_order_v2.fixtures import load_textblock_regions\n",
)
text += r'''

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
'''
test_path.write_text(text, encoding="utf-8")
