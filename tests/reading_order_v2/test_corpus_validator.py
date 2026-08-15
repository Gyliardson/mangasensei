from __future__ import annotations

import inspect
import json

import pytest
from scripts.reading_order_v2.contracts import (
    PAGE_IDS,
    REQUIRED_SLICES,
    ContractError,
    PageGroundTruth,
    QualificationPair,
    arm_asset_paths,
    load_ground_truth,
    validate_corpus_design,
    validate_required_slice_inventory,
)
from scripts.reading_order_v2.run_arm import execute_page


def _design() -> dict[str, object]:
    return {
        "schemaVersion": "reading-order-v2-corpus-design-v1",
        "corpusId": "mangasensei-reading-order-heldout-v2",
        "version": "1.0.0",
        "pageCount": 16,
        "requirements": {
            "minAQualificationPairs": 12,
            "minAPages": 5,
            "minBQualificationPairs": 12,
            "minBPages": 5,
            "minCleanOrdinaryControls": 4,
            "minIntentionalWholePageFallbackPages": 2,
            "minOpenOrIncompleteFramePages": 2,
        },
        "slots": [{"id": page_id, "purpose": page_id} for page_id in PAGE_IDS],
    }


def _annotation(slices: list[str]) -> dict[str, object]:
    return {
        "schemaVersion": "reading-order-v2-annotation-v1",
        "pageId": "H01",
        "readingOrder": ["r0", "r1"],
        "unscoredRegionIds": [],
        "qualificationPairs": [
            {"id": "p0", "earlier": "r0", "later": "r1", "slices": slices}
        ],
        "layoutTags": [],
        "panels": [],
        "orientationExpectations": [],
        "assignmentExpectations": [],
    }


def _page_with_slices(*slices: str) -> PageGroundTruth:
    pairs = tuple(
        QualificationPair(f"p{index}", f"r{index}", f"r{index + 1}", (name,))
        for index, name in enumerate(slices)
    )
    reading_order = tuple(f"r{index}" for index in range(len(slices) + 1))
    return PageGroundTruth("H01", reading_order, (), pairs, (), ())


def test_design_requires_exact_h01_h16_and_frozen_minima() -> None:
    validate_corpus_design(_design())
    missing = _design()
    missing["slots"] = missing["slots"][:-1]
    with pytest.raises(ContractError):
        validate_corpus_design(missing)
    extra = _design()
    extra["slots"] = [*extra["slots"], {"id": "H17", "purpose": "bad"}]
    with pytest.raises(ContractError):
        validate_corpus_design(extra)
    changed = _design()
    changed["requirements"] = dict(changed["requirements"])
    changed["requirements"]["minAQualificationPairs"] = 11
    with pytest.raises(ContractError):
        validate_corpus_design(changed)


def test_annotation_rejects_unknown_qualification_slice(tmp_path) -> None:
    path = tmp_path / "H01.json"
    path.write_text(json.dumps(_annotation(["A", "unknown-slice"])), encoding="utf-8")
    with pytest.raises(ContractError, match="unknown frozen slices"):
        load_ground_truth(path)


@pytest.mark.parametrize("missing_slice", sorted(REQUIRED_SLICES))
def test_required_slice_inventory_rejects_each_missing_slice(missing_slice: str) -> None:
    present = sorted(REQUIRED_SLICES - {missing_slice})
    with pytest.raises(ContractError, match="missing required qualification slices"):
        validate_required_slice_inventory([_page_with_slices(*present)])


def test_complete_frozen_slice_inventory_passes_validation() -> None:
    validate_required_slice_inventory([_page_with_slices(*sorted(REQUIRED_SLICES))])


def test_arm_path_contract_exposes_only_image_and_input(tmp_path) -> None:
    image, fixture = arm_asset_paths(tmp_path, "H01")
    assert image == tmp_path / "images" / "H01.png"
    assert fixture == tmp_path / "inputs" / "H01.json"
    assert "annotation" not in str(image).lower() + str(fixture).lower()
    assert "annotation" not in inspect.signature(execute_page).parameters
