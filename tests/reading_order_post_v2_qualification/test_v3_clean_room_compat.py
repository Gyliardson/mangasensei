from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.reading_order_post_v2_qualification.contracts import PageGroundTruth
from scripts.reading_order_post_v2_qualification.v3_clean_room_compat import (
    C3_REJECTION_ARMS,
    _to_page_ground_truth,
    generic_c3_rejection_pages,
    load_arm_input,
    required_arms_by_page,
)
from scripts.reading_order_v3_authoring import ANNOTATION_SCHEMA_VERSION, INPUT_SCHEMA_VERSION
from scripts.reading_order_v3_authoring.contracts import load_annotation

REPO_ROOT = Path(__file__).resolve().parents[2]
METHODOLOGY = (
    REPO_ROOT
    / "scripts"
    / "reading_order_post_v2_qualification"
    / "spec"
    / "methodology-v3.json"
)
COMPAT_PATH = "scripts/reading_order_post_v2_qualification/v3_clean_room_compat.py"
FROZEN_DIAGNOSTIC_EVALUATOR = "scripts/reading_order_post_v2_qualification/exercise_v3.py"


def _git_blob(path: str) -> str:
    data = (REPO_ROOT / path).read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def test_clean_room_input_maps_exactly_to_canonical_arm_page_input(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": INPUT_SCHEMA_VERSION,
                "pageId": "arbitrary.page-alpha",
                "width": 37,
                "height": 29,
                "regions": [
                    {
                        "regionId": "region / beta",
                        "sourceIndex": 1,
                        "lines": [[[10, 2], [12, 2], [12, 5], [10, 5]]],
                        "angle": -12.5,
                    },
                    {
                        "regionId": "領域 alpha",
                        "sourceIndex": 0,
                        "lines": [[[1, 2], [3, 2], [3, 5], [1, 5]]],
                        "angle": 7,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    page = load_arm_input(path)
    assert page.page_id == "arbitrary.page-alpha"
    assert page.width == 37
    assert page.height == 29
    assert tuple(region.region_id for region in page.regions) == (
        "領域 alpha",
        "region / beta",
    )
    assert tuple(region.source_index for region in page.regions) == (0, 1)
    assert page.regions[0].lines == (((1, 2), (3, 2), (3, 5), (1, 5)),)
    assert page.regions[0].angle == 7.0
    assert page.regions[1].lines == (((10, 2), (12, 2), (12, 5), (10, 5)),)
    assert page.regions[1].angle == -12.5


def test_clean_room_annotation_maps_pairs_exactly_and_derives_no_layout_tags(
    tmp_path: Path,
) -> None:
    path = tmp_path / "annotation.json"
    path.write_text(
        json.dumps(
            {
                "schemaVersion": ANNOTATION_SCHEMA_VERSION,
                "pageId": "page-alpha",
                "readingOrder": ["r first", "r second"],
                "unscoredRegionIds": [],
                "qualificationPairs": [
                    {
                        "id": "pair-alpha",
                        "earlier": "r first",
                        "later": "r second",
                        "slices": ["clean-control"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    clean = load_annotation(path)
    runtime = _to_page_ground_truth(clean)
    assert runtime.page_id == clean.page_id
    assert runtime.reading_order == clean.reading_order
    assert runtime.unscored_region_ids == clean.unscored_region_ids
    assert len(runtime.qualification_pairs) == 1
    assert runtime.qualification_pairs[0].pair_id == "pair-alpha"
    assert runtime.qualification_pairs[0].earlier == "r first"
    assert runtime.qualification_pairs[0].later == "r second"
    assert runtime.qualification_pairs[0].slices == ("clean-control",)
    assert runtime.layout_tags == ()


def test_generic_c3_page_requires_c3_arms_without_category_slice() -> None:
    annotation = PageGroundTruth(
        page_id="page-alpha",
        reading_order=("r1", "r2"),
        unscored_region_ids=(),
        qualification_pairs=(),
        layout_tags=(),
    )
    required = required_arms_by_page(
        (annotation,),
        c3_rejection_page_ids=frozenset({"page-alpha"}),
    )
    assert required == {"page-alpha": C3_REJECTION_ARMS}


def test_generic_c3_rejection_is_counted_once_per_declared_page() -> None:
    diagnostic = {
        "preSegmentation": {"reason": "fewer-than-two-groups", "boxCount": 1},
        "recoveryReason": "rejected-generic-page-witness",
        "assignments": [],
        "relationEdges": [],
        "finalOrder": ["r1", "r2"],
        "fallbackOrder": ["r1", "r2"],
        "usedPanelEvidence": False,
    }
    diagnostics = {
        arm: {"page-alpha": dict(diagnostic)}
        for arm in C3_REJECTION_ARMS
    }
    pages = generic_c3_rejection_pages(
        c3_rejection_page_ids=frozenset({"page-alpha"}),
        diagnostics=diagnostics,
    )
    assert pages == {"page-alpha"}


def test_methodology_binds_clean_room_compatibility_layer() -> None:
    methodology = json.loads(METHODOLOGY.read_text(encoding="utf-8"))
    runtime = methodology["runtimeReachability"]
    assert runtime["evaluatorPath"] == COMPAT_PATH
    assert runtime["evaluatorGitBlobSha"] == _git_blob(COMPAT_PATH)
    adapter = runtime["cleanRoomCompatibility"]
    assert adapter["frozenDiagnosticEvaluatorPath"] == FROZEN_DIAGNOSTIC_EVALUATOR
    assert adapter["frozenDiagnosticEvaluatorGitBlobSha"] == _git_blob(
        FROZEN_DIAGNOSTIC_EVALUATOR
    )
    assert adapter["genericC3RejectionPageMetadataRequired"] is True
    assert adapter["legacyC3CategoryFabricationForbidden"] is True

    trusted = methodology["futureQualificationBoundary"]["runnerContract"][
        "trustedPageInputDerivation"
    ]
    assert trusted["pageLoader"] == "load_arm_input"
    assert trusted["pageLoaderModule"] == COMPAT_PATH
    assert trusted["pageLoaderGitBlobSha"] == _git_blob(COMPAT_PATH)
    mapping = trusted["cleanRoomSerializationMapping"]
    assert mapping["preservedFields"] == [
        "pageId",
        "width",
        "height",
        "regionId",
        "sourceIndex",
        "lines",
        "angle",
    ]
    assert mapping["regionOrdering"] == "ascending-sourceIndex"
    assert mapping["pageIdRenamingForbidden"] is True
    assert mapping["geometryInferenceForbidden"] is True
    assert mapping["annotationDependentTransformationForbidden"] is True
    assert mapping["candidateAccessForbidden"] is True
