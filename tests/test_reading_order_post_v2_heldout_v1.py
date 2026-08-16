from __future__ import annotations

import hashlib
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
CORPUS_SNAPSHOT = {
    path.relative_to(CORPUS_ROOT).as_posix(): path.read_bytes()
    for path in sorted(CORPUS_ROOT.rglob("*"))
    if path.is_file()
}


def _snapshot_json(relative: str) -> dict[str, object]:
    return json.loads(CORPUS_SNAPSHOT[relative].decode("utf-8"))


def _materialize_snapshot(root: Path) -> None:
    for relative, payload in CORPUS_SNAPSHOT.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def _authored_counts() -> tuple[int, int, dict[str, set[str]], dict[str, int]]:
    design = _snapshot_json("corpus-design.json")
    scored_regions = 0
    qualification_pairs = 0
    slice_pages: dict[str, set[str]] = defaultdict(set)
    slice_pairs: dict[str, int] = defaultdict(int)

    for page_id in design["pageIds"]:
        assert isinstance(page_id, str)
        page_input = _snapshot_json(f"inputs/{page_id}.json")
        annotation = _snapshot_json(f"annotations/{page_id}.json")
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


def test_post_v2_heldout_v1_contract_manifest_and_historical_guard(
    tmp_path: Path,
) -> None:
    manifest = _snapshot_json("manifest.json")
    inventory = manifest["inventory"]
    assert isinstance(inventory, list)
    mismatches: list[tuple[str, str, str]] = []
    for item in inventory:
        assert isinstance(item, dict)
        relative = item["file"]
        expected = item["sha256"]
        assert isinstance(relative, str)
        assert isinstance(expected, str)
        actual = hashlib.sha256(CORPUS_SNAPSHOT[relative]).hexdigest()
        if actual != expected:
            mismatches.append((relative, expected, actual))
    if mismatches:
        details = "\n".join(
            f"{relative} expected={expected} actual={actual}"
            for relative, expected, actual in mismatches
        )
        raise AssertionError(details)

    snapshot_root = tmp_path / "heldout-v1"
    _materialize_snapshot(snapshot_root)
    validate_corpus(snapshot_root)
    assert_no_historical_v2_content_reuse(snapshot_root)

    design = _snapshot_json("corpus-design.json")
    assert design["corpusId"] == "mangasensei-reading-order-post-v2-heldout-v1"
    assert design["version"] == "1.0.0"
    assert design["authorshipBoundary"] == "new-project-authored-no-historical-v2-case-reuse"
    assert manifest["corpusId"] == design["corpusId"]
    assert manifest["version"] == design["version"]

    manifest_sha = hashlib.sha256(CORPUS_SNAPSHOT["manifest.json"]).hexdigest()
    assert manifest_sha == "f33fe44bf30521958f09f904e8031e079789120a2f2d0c341480eca0b20d00f4"


def test_post_v2_heldout_v1_png_and_gt_input_integrity() -> None:
    design = _snapshot_json("corpus-design.json")
    for page_id in design["pageIds"]:
        assert isinstance(page_id, str)
        png = CORPUS_SNAPSHOT[f"images/{page_id}.png"]
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert png[12:16] == b"IHDR"
        width, height, depth, color_type, compression, filter_method, interlace = (
            struct.unpack(">IIBBBBB", png[16:29])
        )
        assert (depth, color_type, compression, filter_method, interlace) == (8, 2, 0, 0, 0)

        page_input = _snapshot_json(f"inputs/{page_id}.json")
        annotation = _snapshot_json(f"annotations/{page_id}.json")
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
    design = _snapshot_json("corpus-design.json")
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

    # These counts prove authored scenario coverage only.
    # They do not run or inspect candidate diagnostics.
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
