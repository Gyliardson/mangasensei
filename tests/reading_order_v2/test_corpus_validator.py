from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from scripts.reading_order_v2.contracts import ReadingOrderV2ContractError
from scripts.reading_order_v2.run_arm import run_arm
from scripts.reading_order_v2.validate_design import validate_design


def test_committed_design_freezes_exact_h01_through_h16() -> None:
    design = validate_design(
        Path("assets/reading-order-v2/heldout-v1/corpus-design.json")
    )
    assert [slot["id"] for slot in design["slots"]] == [
        f"H{index:02d}" for index in range(1, 17)
    ]
    assert design["minimumCoverage"] == {
        "pageCount": 16,
        "aQualificationPairs": 12,
        "aQualificationPages": 5,
        "bQualificationPairs": 12,
        "bQualificationPages": 5,
        "cleanOrdinaryControls": 4,
        "intentionalWholePageFallbackPages": 2,
        "independentOpenIncompleteFramePages": 2,
    }


def test_design_validator_rejects_missing_or_extra_slot(tmp_path: Path) -> None:
    source = Path("assets/reading-order-v2/heldout-v1/corpus-design.json")
    raw = json.loads(source.read_text(encoding="utf-8"))
    raw["slots"] = raw["slots"][:-1]
    path = tmp_path / "design.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ReadingOrderV2ContractError, match="H01..H16"):
        validate_design(path)


def test_arm_runner_interface_has_no_annotation_or_ground_truth_input() -> None:
    parameters = set(inspect.signature(run_arm).parameters)
    assert parameters == {
        "corpus_root",
        "page_id",
        "arm",
        "repository_sha",
        "output_dir",
    }
    assert not any("annotation" in name.lower() or "ground" in name.lower() for name in parameters)
