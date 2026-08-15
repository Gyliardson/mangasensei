from __future__ import annotations

import inspect

import pytest
from scripts.reading_order_v2.contracts import (
    PAGE_IDS,
    ContractError,
    arm_asset_paths,
    validate_corpus_design,
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


def test_arm_path_contract_exposes_only_image_and_input(tmp_path) -> None:
    image, fixture = arm_asset_paths(tmp_path, "H01")
    assert image == tmp_path / "images" / "H01.png"
    assert fixture == tmp_path / "inputs" / "H01.json"
    assert "annotation" not in str(image).lower() + str(fixture).lower()
    assert "annotation" not in inspect.signature(execute_page).parameters
