from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image
from scripts.reading_order_v2.contracts import (
    PAGE_IDS,
    REQUIRED_SLICES,
    load_arm_input,
    load_ground_truth,
)
from scripts.reading_order_v2.freeze import freeze_manifest
from scripts.reading_order_v2.validate_corpus import validate_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "assets" / "reading-order-v2" / "heldout-v1"
EXPECTED = {f"H{index:02d}" for index in range(1, 17)}


def _names(directory: str, suffix: str) -> set[str]:
    return {path.stem for path in (CORPUS_ROOT / directory).glob(f"*{suffix}")}


def test_exact_h01_h16_inventory_and_frozen_manifest() -> None:
    assert set(PAGE_IDS) == EXPECTED
    assert _names("source", ".svg") == EXPECTED
    assert _names("images", ".png") == EXPECTED
    assert _names("inputs", ".json") == EXPECTED
    assert _names("annotations", ".json") == EXPECTED
    required_common = {
        "LICENSE",
        "NOTICE.md",
        "README.md",
        "corpus-design.json",
        "manifest.json",
        "provenance/toolchain.json",
    }
    assert all((CORPUS_ROOT / relative).is_file() for relative in required_common)
    validate_corpus(CORPUS_ROOT)
    frozen = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert frozen == freeze_manifest(CORPUS_ROOT)


def test_source_svg_contract_and_png_contract() -> None:
    for page_id in PAGE_IDS:
        source_path = CORPUS_ROOT / "source" / f"{page_id}.svg"
        source = source_path.read_text(encoding="utf-8")
        lowered = source.lower()
        assert "http://" not in lowered
        assert "https://" not in lowered
        assert "<text" not in lowered
        assert "<image" not in lowered
        assert "<foreignobject" not in lowered
        root = ElementTree.fromstring(source)  # noqa: S314 -- project-authored SVG
        assert root.attrib["width"] == "1440"
        assert root.attrib["height"] == "2048"
        assert root.attrib["data-page-id"] == page_id
        forbidden = {"text", "image", "foreignobject"}
        for element in root.iter():
            local_name = element.tag.rsplit("}", 1)[-1].lower()
            assert local_name not in forbidden
            for value in element.attrib.values():
                value_lower = value.lower()
                assert "http://" not in value_lower
                assert "https://" not in value_lower
        with Image.open(CORPUS_ROOT / "images" / f"{page_id}.png") as image:
            assert image.format == "PNG"
            assert image.mode == "RGB"
            assert image.size == (1440, 2048)


def test_input_annotation_alignment_and_coverage_minima() -> None:
    a_pairs = b_pairs = 0
    a_pages: set[str] = set()
    b_pages: set[str] = set()
    clean_pages = fallback_pages = open_pages = 0
    slice_counts = {name: 0 for name in REQUIRED_SLICES}
    for page_id in PAGE_IDS:
        input_path = CORPUS_ROOT / "inputs" / f"{page_id}.json"
        annotation_path = CORPUS_ROOT / "annotations" / f"{page_id}.json"
        raw_input = json.loads(input_path.read_text(encoding="utf-8"))
        raw_annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
        assert set(raw_input) == {"schemaVersion", "pageId", "width", "height", "regions"}
        assert set(raw_annotation) == {
            "schemaVersion",
            "pageId",
            "readingOrder",
            "unscoredRegionIds",
            "qualificationPairs",
            "layoutTags",
            "panels",
            "orientationExpectations",
            "assignmentExpectations",
        }
        assert raw_input["width"] == 1440
        assert raw_input["height"] == 2048
        assert raw_input["pageId"] == raw_annotation["pageId"] == page_id
        page = load_arm_input(input_path)
        gt = load_ground_truth(annotation_path)
        input_ids = {region.region_id for region in page.regions}
        assert [region.source_index for region in page.regions] == list(range(len(page.regions)))
        assert input_ids == set(gt.reading_order) | set(gt.unscored_region_ids)
        assert not (set(gt.reading_order) & set(gt.unscored_region_ids))
        assert {
            item["regionId"] for item in raw_annotation["orientationExpectations"]
        } == input_ids
        assert {
            item["regionId"] for item in raw_annotation["assignmentExpectations"]
        } == input_ids
        a_count = sum(
            "A" in pair.slices or "A+B" in pair.slices
            for pair in gt.qualification_pairs
        )
        b_count = sum(
            "B" in pair.slices or "A+B" in pair.slices
            for pair in gt.qualification_pairs
        )
        a_pairs += a_count
        b_pairs += b_count
        if a_count:
            a_pages.add(page_id)
        if b_count:
            b_pages.add(page_id)
        for pair in gt.qualification_pairs:
            for slice_name in pair.slices:
                slice_counts[slice_name] += 1
        clean_pages += int("clean-control" in gt.layout_tags)
        fallback_pages += int("intentional-fallback" in gt.layout_tags)
        open_pages += int("open-frame" in gt.layout_tags or "incomplete-frame" in gt.layout_tags)
        panel_ids = [panel.panel_id for panel in gt.panels]
        assert len(panel_ids) == len(set(panel_ids))
        precedence = [
            panel.precedence_position
            for panel in gt.panels
            if panel.precedence_position is not None
        ]
        assert precedence == list(range(len(precedence)))
        for panel in gt.panels:
            assert 0 <= panel.bbox.x < 1440
            assert 0 <= panel.bbox.y < 2048
            assert panel.bbox.width > 0
            assert panel.bbox.height > 0
            assert panel.bbox.x + panel.bbox.width <= 1440
            assert panel.bbox.y + panel.bbox.height <= 2048
    assert a_pairs >= 12
    assert len(a_pages) >= 5
    assert b_pairs >= 12
    assert len(b_pages) >= 5
    assert clean_pages >= 4
    assert fallback_pages >= 2
    assert open_pages >= 2
    assert all(count > 0 for count in slice_counts.values())
