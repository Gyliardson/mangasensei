from __future__ import annotations

import json
import struct
from collections import defaultdict
from pathlib import Path

from scripts.reading_order_post_v2_qualification.contracts import (
    DESIGN_REQUIREMENTS,
    EXERCISE_MINIMA,
    SLICE_MINIMA,
    validate_corpus,
)
from scripts.reading_order_post_v2_qualification.historical_guard import (
    assert_no_historical_v2_content_reuse,
)

CORPUS_ROOT = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "reading-order-post-v2"
    / "heldout-v1"
)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _authored_counts() -> tuple[int, int, dict[str, set[str]], dict[str, int]]:
    design = _load_json(CORPUS_ROOT / "corpus-design.json")
    scored_regions = 0
    qualification_pairs = 0
    slice_pages: dict[str, set[str]] = defaultdict(set)
    slice_pairs: dict[str, int] = defaultdict(int)

    for page_id in design["pageIds"]:
        assert isinstance(page_id, str)
        page_input = _load_json(CORPUS_ROOT / "inputs" / f"{page_id}.json")
        annotation = _load_json(CORPUS_ROOT / "annotations" / f"{page_id}.json")
        regions = page_input["regions"]
        reading_order = annotation["readingOrder"]
        unscored = annotation["unscoredRegionIds"]
        pairs = annotation["qualificationPairs"]
        assert isinstance(regions, list)
        assert isinstance(reading_order, list)
        assert isinstance(unscored, list)
        assert isinstance(pairs, list)

        scored_regions += len(regions) - len(unscored)
        qualification_pairs += len(pairs)
        for pair in pairs:
            assert isinstance(pair, dict)
            slices = pair["slices"]
            assert isinstance(slices, list)
            for slice_name in slices:
                assert isinstance(slice_name, str)
                slice_pages[slice_name].add(page_id)
                slice_pairs[slice_name] += 1

    return scored_regions, qualification_pairs, slice_pages, slice_pairs


def test_post_v2_heldout_v1_contract_manifest_and_historical_guard() -> None:
    validate_corpus(CORPUS_ROOT)
    assert_no_historical_v2_content_reuse(CORPUS_ROOT)

    design = _load_json(CORPUS_ROOT / "corpus-design.json")
    manifest = _load_json(CORPUS_ROOT / "manifest.json")
    assert design["corpusId"] == "mangasensei-reading-order-post-v2-heldout-v1"
    assert design["version"] == "1.0.0"
    assert design["authorshipBoundary"] == "new-project-authored-no-historical-v2-case-reuse"
    assert manifest["corpusId"] == design["corpusId"]
    assert manifest["version"] == design["version"]


def test_post_v2_heldout_v1_png_and_gt_input_integrity() -> None:
    design = _load_json(CORPUS_ROOT / "corpus-design.json")
    for page_id in design["pageIds"]:
        assert isinstance(page_id, str)
        png = (CORPUS_ROOT / "images" / f"{page_id}.png").read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert png[12:16] == b"IHDR"
        width, height, depth, color_type, compression, filter_method, interlace = (
            struct.unpack(">IIBBBBB", png[16:29])
        )
        assert (depth, color_type, compression, filter_method, interlace) == (8, 2, 0, 0, 0)

        page_input = _load_json(CORPUS_ROOT / "inputs" / f"{page_id}.json")
        annotation = _load_json(CORPUS_ROOT / "annotations" / f"{page_id}.json")
        assert (width, height) == (page_input["width"], page_input["height"])

        regions = page_input["regions"]
        assert isinstance(regions, list)
        region_ids = {region["regionId"] for region in regions if isinstance(region, dict)}
        source_indexes = [region["sourceIndex"] for region in regions if isinstance(region, dict)]
        assert source_indexes == list(range(len(regions)))

        reading_order = annotation["readingOrder"]
        unscored = annotation["unscoredRegionIds"]
        assert isinstance(reading_order, list)
        assert isinstance(unscored, list)
        assert region_ids == set(reading_order)
        assert set(unscored).issubset(region_ids)

        positions = {region_id: index for index, region_id in enumerate(reading_order)}
        pairs = annotation["qualificationPairs"]
        assert isinstance(pairs, list)
        for pair in pairs:
            assert isinstance(pair, dict)
            earlier = pair["earlier"]
            later = pair["later"]
            assert isinstance(earlier, str)
            assert isinstance(later, str)
            assert positions[earlier] < positions[later]


def test_post_v2_heldout_v1_frozen_minima_and_authored_exercise_coverage() -> None:
    design = _load_json(CORPUS_ROOT / "corpus-design.json")
    page_ids = design["pageIds"]
    assert isinstance(page_ids, list)
    scored_regions, pair_count, slice_pages, slice_pairs = _authored_counts()

    assert len(page_ids) >= DESIGN_REQUIREMENTS["minimumPageCount"]
    assert scored_regions >= DESIGN_REQUIREMENTS["minimumScoredRegions"]
    assert pair_count >= DESIGN_REQUIREMENTS["minimumQualificationPairs"]
    assert len(slice_pages["combined-c1-c2-c3-b1"]) >= DESIGN_REQUIREMENTS[
        "minimumCombinedMechanismPages"
    ]
    assert len(slice_pages["intentional-fallback"]) >= DESIGN_REQUIREMENTS[
        "minimumIntentionalFallbackPages"
    ]
    assert len(slice_pages["clean-control"]) >= DESIGN_REQUIREMENTS["minimumCleanControlPages"]

    for slice_name, minimum in SLICE_MINIMA.items():
        assert len(slice_pages[slice_name]) >= minimum["minPages"]
        assert slice_pairs[slice_name] >= minimum["minPairs"]

    # These counts prove authored scenario coverage only. They do not run or inspect candidate diagnostics.
    authored_exercise_slices = {
        "c1_guarded_pairs": "c1-boundary-positive",
        "c2_gutter_pairs": "c2-gutter-bridge",
        "c2_overlap_pairs": "c2-ambiguous-overlap-bridge",
        "c2_pair_precedence_pairs": "c2-pair-precedence-slot",
        "c2_fail_closed_no_relation_pairs": "c2-one-sided-non-unique-fail-closed",
        "c2_conflict_cycle_fallback_pairs": "c2-conflict-cycle-safety",
        "c3_positive_pairs": "c3-positive-recovery",
        "c3_zero_multiple_anchor_rejection_pairs": "c3-zero-multiple-anchor-negative",
        "c3_zero_multiple_companion_rejection_pairs": "c3-zero-multiple-companion-negative",
        "c3_invalid_topology_rejection_pairs": "c3-invalid-topology-negative",
        "c3_insufficient_visible_support_rejection_pairs": (
            "c3-insufficient-visible-support-negative"
        ),
        "b1_horizontal_pairs": "b1-horizontal",
        "b1_vertical_pairs": "b1-vertical",
        "b1_mixed_pairs": "b1-mixed-orientation",
    }
    assert set(authored_exercise_slices) == set(EXERCISE_MINIMA)
    for exercise_name, slice_name in authored_exercise_slices.items():
        assert slice_pairs[slice_name] >= EXERCISE_MINIMA[exercise_name]
