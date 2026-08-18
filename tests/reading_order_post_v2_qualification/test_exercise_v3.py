from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import scripts.reading_order_post_v2_qualification.run_arm as run_arm_module
from PIL import Image
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
    load_arm_input,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import (
    EXERCISE_MINIMA_V3,
    V3_INVALID_DIAGNOSTIC_HARNESS_STATUS,
    V3_INVALID_EXPERIMENT_CLASSIFICATION,
    V3DiagnosticValidationError,
    V3TrustedPageInput,
    build_exercise_report_v3,
)

import mangasensei.ocr.diagnostics.reading_order_post_v2_calibration as candidate_module
from mangasensei.ocr.reading_order import PanelBox, PanelSegmentation

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT = "/usr/bin/git"
METHODOLOGY_PATH = (
    REPO_ROOT
    / "scripts"
    / "reading_order_post_v2_qualification"
    / "spec"
    / "methodology-v3.json"
)
V2_SPEC_PATH = (
    REPO_ROOT
    / "scripts"
    / "reading_order_post_v2_qualification"
    / "spec"
    / "experiment-spec-v2.json"
)
WORKFLOW_PATH = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "reading-order-post-v2-qualification.yml"
)
V3_EXPERIMENT_ID = "reading-order-post-v2-c1-c2-c3-b1-v3"
EXECUTION_SHA = "f45facb2284d740df2f294800f705414e0ba465e"
PAGE_SIZE = 1000


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_blob(path: str) -> str:
    return subprocess.run(  # noqa: S603
        [GIT, "rev-parse", f"HEAD:{path}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _quad(box: tuple[int, int, int, int]) -> list[list[int]]:
    x1, y1, x2, y2 = box
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _write_corpus(
    root: Path,
    *,
    page_id: str,
    region_boxes: tuple[tuple[int, int, int, int], ...],
) -> None:
    (root / "inputs").mkdir(parents=True)
    (root / "images").mkdir(parents=True)
    payload = {
        "schemaVersion": "reading-order-post-v2-input-v1",
        "pageId": page_id,
        "width": PAGE_SIZE,
        "height": PAGE_SIZE,
        "regions": [
            {
                "regionId": f"r{index + 1}",
                "sourceIndex": index,
                "lines": [_quad(box)],
                "angle": 0,
            }
            for index, box in enumerate(region_boxes)
        ],
    }
    (root / "inputs" / f"{page_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    Image.fromarray(np.full((PAGE_SIZE, PAGE_SIZE, 3), 255, dtype=np.uint8)).save(
        root / "images" / f"{page_id}.png"
    )


def _trusted_input(root: Path, page_id: str) -> V3TrustedPageInput:
    page = load_arm_input(root / "inputs" / f"{page_id}.json")
    with Image.open(root / "images" / f"{page_id}.png") as opened:
        pixels = np.asarray(opened.convert("RGB"))
    return V3TrustedPageInput(page=page, pixels=pixels)


def _set_allowed_seams(
    monkeypatch: pytest.MonkeyPatch,
    *,
    panels: tuple[PanelBox, ...],
    reliable: bool = True,
    reason: str = "reliable",
    lines: tuple[tuple[float, float, float, float], ...] | None = None,
) -> None:
    segmentation = PanelSegmentation(panels, reliable, reason)
    monkeypatch.setattr(candidate_module, "segment_panel_groups", lambda _pixels: segmentation)
    monkeypatch.setattr(run_arm_module, "segment_panel_groups", lambda _pixels: segmentation)
    if lines is not None:
        monkeypatch.setattr(candidate_module, "_line_segments", lambda _gray: lines)


def _annotation(
    *,
    page_id: str,
    slice_name: str,
    region_count: int,
    pair: tuple[str, str],
) -> PageGroundTruth:
    return PageGroundTruth(
        page_id=page_id,
        reading_order=tuple(f"r{index + 1}" for index in range(region_count)),
        unscored_region_ids=(),
        qualification_pairs=(QualificationPair("p1", pair[0], pair[1], (slice_name,)),),
        layout_tags=(),
    )


def _producer_case(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slice_name: str,
    arms: tuple[ArmId, ...],
    panels: tuple[PanelBox, ...],
    regions: tuple[tuple[int, int, int, int], ...],
    pair: tuple[str, str],
    reliable: bool = True,
    reason: str = "reliable",
    lines: tuple[tuple[float, float, float, float], ...] | None = None,
) -> tuple[
    PageGroundTruth,
    dict[ArmId, dict[str, dict[str, object]]],
    dict[str, V3TrustedPageInput],
]:
    root = tmp_path / "corpus"
    _write_corpus(root, page_id="Q901", region_boxes=regions)
    _set_allowed_seams(
        monkeypatch,
        panels=panels,
        reliable=reliable,
        reason=reason,
        lines=lines,
    )
    diagnostics: dict[ArmId, dict[str, dict[str, object]]] = {}
    for arm in arms:
        diagnostic_path, _ = run_arm_module.execute_page(
            corpus_root=root,
            page_id="Q901",
            arm_id=arm,
            execution_sha=EXECUTION_SHA,
            repeat=1,
            output_root=root / "output",
        )
        raw = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        diagnostics[arm] = {"Q901": raw}
    page = _annotation(
        page_id="Q901",
        slice_name=slice_name,
        region_count=len(regions),
        pair=pair,
    )
    return page, diagnostics, {"Q901": _trusted_input(root, "Q901")}


def _assert_invalid(
    *,
    page: PageGroundTruth,
    diagnostics: Any,
    trusted: dict[str, V3TrustedPageInput],
    expected_fragment: str,
) -> V3DiagnosticValidationError:
    with pytest.raises(V3DiagnosticValidationError) as caught:
        build_exercise_report_v3(
            annotations=(page,),
            diagnostics=diagnostics,
            trusted_page_inputs=trusted,
        )
    assert caught.value.classification == V3_INVALID_EXPERIMENT_CLASSIFICATION
    assert caught.value.harness_status == V3_INVALID_DIAGNOSTIC_HARNESS_STATUS
    assert expected_fragment in str(caught.value)
    return caught.value


def _group_regions(diagnostic: dict[str, object]) -> dict[str, list[str]]:
    raw = diagnostic["assignments"]
    assert isinstance(raw, list)
    groups: dict[str, list[str]] = {}
    for item in raw:
        assert isinstance(item, dict)
        region_id = item["regionId"]
        assigned = item["assignedGroupIndex"]
        if isinstance(region_id, str) and type(assigned) is int:
            groups.setdefault(f"g{assigned:03d}", []).append(region_id)
    return groups


def _uncertain_regions(diagnostic: dict[str, object]) -> dict[str, str]:
    raw = diagnostic["assignments"]
    assert isinstance(raw, list)
    result: dict[str, str] = {}
    for item in raw:
        assert isinstance(item, dict)
        region_id = item["regionId"]
        label = item["uncertainNodeLabel"]
        if isinstance(region_id, str) and isinstance(label, str):
            result[label] = region_id
    return result


def _materialized_blocks(diagnostic: dict[str, object]) -> list[list[str]]:
    node_order = diagnostic["nodeOrder"]
    assert isinstance(node_order, list)
    groups = _group_regions(diagnostic)
    uncertain = _uncertain_regions(diagnostic)
    return [
        groups.get(node, [uncertain[node]] if node in uncertain else [])
        for node in node_order
        if groups.get(node) or node in uncertain
    ]


def test_v3_methodology_is_frozen_but_not_currently_dispatchable() -> None:
    methodology = json.loads(METHODOLOGY_PATH.read_text(encoding="utf-8"))
    assert methodology["schemaVersion"] == "reading-order-post-v2-methodology-v3"
    assert methodology["experimentId"] == V3_EXPERIMENT_ID
    assert methodology["status"] == "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION"
    assert methodology["currentWorkflowDispatchable"] is False
    base = methodology["baseV2Spec"]
    assert base["sha256"] == _sha256(V2_SPEC_PATH)
    assert base["gitBlobSha"] == _git_blob(base["path"])
    candidate = methodology["candidateBinding"]
    assert candidate["commitSha"] == EXECUTION_SHA
    assert candidate["gitBlobSha"] == _git_blob(candidate["path"])
    runtime = methodology["runtimeReachability"]
    assert runtime["evaluatorGitBlobSha"] == _git_blob(runtime["evaluatorPath"])
    calibration = runtime["calibrationSet"]
    assert calibration["candidateReachabilityTestGitBlobSha"] == _git_blob(
        calibration["candidateReachabilityTestPath"]
    )
    assert calibration["evaluatorContractTestGitBlobSha"] == _git_blob(
        calibration["evaluatorContractTestPath"]
    )
    assert runtime["exerciseMinima"] == EXERCISE_MINIMA_V3
    invalid = runtime["invalidDiagnosticSemantics"]
    assert invalid["classification"] == V3_INVALID_EXPERIMENT_CLASSIFICATION
    assert invalid["harnessStatus"] == V3_INVALID_DIAGNOSTIC_HARNESS_STATUS
    assert invalid["countsForbidden"] is True

    authenticity = runtime["evidenceAuthenticityLayers"]
    assert authenticity["envelopeStructuralValidity"]["category"] == "CATEGORY_A"
    assert authenticity["envelopeStructuralValidity"]["provesProducerAuthenticityAlone"] is False
    assert authenticity["trustedInputBinding"]["category"] == "CATEGORY_B"
    assert authenticity["producerSemanticAuthentication"]["category"] == "CATEGORY_B"
    assert authenticity["producerSemanticAuthentication"]["usesFrozenCandidateDirectly"] is True
    producer_auth = authenticity["producerSemanticAuthentication"]
    assert producer_auth["manualEquivalentAlgorithmForbidden"] is True
    assert producer_auth["arbitraryDiagnosticClaimsAccepted"] is False
    assert "finalOrder" in producer_auth["recomputedFields"]
    assert producer_auth["finalOrderPolicy"] == "exact-frozen-producer-recomputation"

    production = runtime["productionDiagnosticContract"]
    assert production["serializationReference"].endswith("/run_arm.py")
    assert production["nodeGraph"]["relationPanelEndpointsMustBeActiveGroups"] is True
    assert production["nodeGraph"]["overlapEndpointsEqualCandidateGroups"] is True
    assert production["nodeGraph"]["genericRelationExactDerivability"] == (
        "frozen-producer-recomputation"
    )
    assert production["finalOrder"]["panelBlocksFollowNodeOrder"] is True
    assert production["finalOrder"]["categoryAStructuralBlockValidationRetained"] is True
    assert production["finalOrder"]["panelInternalOrderProducerAuthenticated"] is True
    assert production["finalOrder"]["manualB0B1ReimplementationForbidden"] is True
    assert production["assignments"]["regionIdSourceIndexBoundToTrustedArmPageInput"] is True
    assert production["producerDerivedFacts"]["trustedPixelsRequired"] is True
    assert production["producerDerivedFacts"]["finalOrderRecomputed"] is True

    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert V3_EXPERIMENT_ID not in workflow
    assert "methodology-v3.json" not in workflow


def test_v3_methodology_separates_authoring_coverage_from_runtime_reachability() -> None:
    methodology = json.loads(METHODOLOGY_PATH.read_text(encoding="utf-8"))
    authoring = methodology["authoringCoverage"]
    runtime = methodology["runtimeReachability"]
    assert authoring["candidateIndependent"] is True
    assert authoring["cannotProveRuntimeExercise"] is True
    assert runtime["calibrationSet"]["heldoutEvidence"] is False
    assert runtime["calibrationSet"]["candidateInspectionAllowed"] is True
    assert runtime["c3NegativeSemantics"] == "generic-page-rejection-only"
    assert runtime["c3NegativeWitnessUnit"] == "unique-page"
    assert runtime["positiveGateIndependence"]["dedicatedMechanismPagesRequired"] is True


def test_v3_methodology_freezes_future_runner_root_of_trust() -> None:
    methodology = json.loads(METHODOLOGY_PATH.read_text(encoding="utf-8"))
    future = methodology["futureQualificationBoundary"]
    runner = future["runnerContract"]
    assert runner["contractFreezeOnly"] is True
    assert runner["executableRunnerImplementedHere"] is False

    preflight = runner["inheritedFailClosedPreflight"]
    assert preflight["requiredBeforePrimaryCandidateEvaluation"] is True
    assert preflight["requiredBeforeProducerAuthenticationRecomputation"] is True
    assert preflight["secondPreflightImplementationForbidden"] is True
    assert preflight["exactExecutionShaRequired"] is True
    assert preflight["exactExecutionTreeRequired"] is True
    assert preflight["cleanCheckoutAndWorktreeRequired"] is True
    assert preflight["headCandidateBlobMustEqualFrozenCandidateGitBlobSha"] is True
    assert preflight["allApplicableInheritedFrozenSourceBindingsRequired"] is True

    corpus = runner["sealedCorpusIntegrity"]
    assert corpus == {
        "sealedCorpusOnly": True,
        "manifestIntegrityRequired": True,
        "designIntegrityRequired": True,
        "inventoryIntegrityRequired": True,
        "inputIntegrityRequired": True,
        "imageIntegrityRequired": True,
    }

    trusted = runner["trustedPageInputDerivation"]
    assert trusted["pageDerivedInternallyOnly"] is True
    assert trusted["pageLoader"] == "load_arm_input"
    assert trusted["pixelsDerivedInternallyOnly"] is True
    assert trusted["callerSuppliedArmPageInputForbidden"] is True
    assert trusted["callerSuppliedPixelBufferForbidden"] is True
    assert trusted["bindingChain"] == [
        "sealed manifest inventory",
        "exact input bytes",
        "exact image bytes",
        "pageId",
        "ArmPageInput",
        "decoded pixels",
    ]

    pixels = runner["canonicalPixels"]
    assert pixels["decodedFromVerifiedSealedPng"] is True
    assert pixels["colorMode"] == "RGB"
    assert pixels["dtype"] == "uint8"
    assert pixels["shape"] == "H x W x 3"
    assert pixels["privateStableSnapshot"] is True
    assert pixels["replacementOrMutationBetweenPrimaryAndAuthenticationForbidden"] is True

    source = runner["candidateRuntimeAuthenticity"]
    assert source["producerAuthenticationValidOnlyAfterSourceBindingPreflight"] is True
    assert source["monkeypatchAndTestSeamsForbidden"] is True
    assert source["moduleSubstitutionForbidden"] is True
    assert source["cleanAuthenticatedSourceStateRequired"] is True
    assert source["sameFrozenCalibrationConfigSemanticsPerArm"] is True
    assert source["syntheticCalibrationMonkeypatchModelIsQualificationEvidence"] is False


def test_v3_methodology_freezes_recomputation_as_same_qualification_verification() -> None:
    methodology = json.loads(METHODOLOGY_PATH.read_text(encoding="utf-8"))
    recomputation = methodology["futureQualificationBoundary"]["runnerContract"][
        "producerAuthenticationRecomputation"
    ]
    assert recomputation["intraHarnessVerificationWithinSameQualification"] is True
    assert recomputation["secondWorkflowDispatch"] is False
    assert recomputation["secondQualification"] is False
    assert recomputation["replay"] is False
    assert recomputation["newQualificationIdentity"] is False
    assert recomputation["additionalStandaloneAuthorization"] is False
    assert recomputation["oneQualificationRuleAppliesToWorkflowExecutionIdentity"] is True
    assert recomputation["sameFrozenInputRequired"] is True
    assert recomputation["sameAuthenticatedSourceRequired"] is True
    assert recomputation["sameArmConfigRequired"] is True
    assert recomputation["sameQualificationExecutionContextRequired"] is True


def test_v3_methodology_binds_diagnostic_execution_sha_to_authorized_execution() -> None:
    methodology = json.loads(METHODOLOGY_PATH.read_text(encoding="utf-8"))
    binding = methodology["futureQualificationBoundary"]["runnerContract"][
        "diagnosticExecutionShaBinding"
    ]
    assert binding["everyDiagnosticMustEqualAuthorizedExecutionSha"] is True
    assert binding["authorizedExecutionShaSource"] == (
        "qualification identity plus inherited fail-closed preflight"
    )
    assert binding["sharedSyntacticallyValidShaAloneIsInsufficient"] is True


def test_v3_final_order_emitted_by_producer_is_authenticated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="b1-horizontal",
        arms=(ArmId.B1_ONLY, ArmId.C1_C2_C3_B1),
        panels=(PanelBox(0, 0, 120, 120), PanelBox(200, 0, 320, 120)),
        regions=(
            (20, 20, 40, 40),
            (60, 50, 80, 70),
            (220, 20, 240, 40),
            (260, 50, 280, 70),
        ),
        pair=("r1", "r2"),
    )
    report = build_exercise_report_v3(
        annotations=(page,),
        diagnostics=diagnostics,
        trusted_page_inputs=trusted,
    )
    assert report.counts["b1_horizontal_pairs"].count == 1


def test_v3_final_order_rejects_internal_panel_order_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="b1-horizontal",
        arms=(ArmId.B1_ONLY, ArmId.C1_C2_C3_B1),
        panels=(PanelBox(0, 0, 120, 120), PanelBox(200, 0, 320, 120)),
        regions=(
            (20, 20, 40, 40),
            (60, 50, 80, 70),
            (220, 20, 240, 40),
            (260, 50, 280, 70),
        ),
        pair=("r1", "r2"),
    )
    target = diagnostics[ArmId.B1_ONLY]["Q901"]
    groups = _group_regions(target)
    multi = next(regions for regions in groups.values() if len(regions) >= 2)
    final_order = target["finalOrder"]
    assert isinstance(final_order, list)
    original = list(final_order)
    positions = [final_order.index(region_id) for region_id in multi[:2]]
    final_order[positions[0]], final_order[positions[1]] = (
        final_order[positions[1]],
        final_order[positions[0]],
    )
    assert set(final_order) == set(original)
    assert target["nodeOrder"] == diagnostics[ArmId.B1_ONLY]["Q901"]["nodeOrder"]
    assert target["assignments"] == diagnostics[ArmId.B1_ONLY]["Q901"]["assignments"]
    assert _materialized_blocks(target) == _materialized_blocks(
        diagnostics[ArmId.B1_ONLY]["Q901"]
    )

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="finalOrder: does not match frozen producer recomputation",
    )


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("uncertain-wrong-slot", "uncertain region must occupy its nodeOrder slot"),
        ("interleaved-groups", "panel node as one contiguous block"),
        ("inverted-group-blocks", "panel node as one contiguous block"),
        ("region-moved-to-other-block", "panel node as one contiguous block"),
    ],
)
def test_v3_final_order_must_follow_node_order_blocks(
    mutation: str,
    expected_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    if mutation == "uncertain-wrong-slot":
        page, diagnostics, trusted = _producer_case(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            slice_name="c2-one-sided-non-unique-fail-closed",
            arms=(ArmId.C2_ONLY,),
            panels=(PanelBox(0, 0, 120, 120), PanelBox(0, 220, 120, 340)),
            regions=(
                (20, 20, 40, 40),
                (60, 50, 80, 70),
                (20, 240, 40, 260),
                (180, 150, 220, 190),
            ),
            pair=("r1", "r4"),
        )
        target = diagnostics[ArmId.C2_ONLY]["Q901"]
        uncertain = _uncertain_regions(target)
        assert uncertain
        uncertain_region = next(iter(uncertain.values()))
        final_order = target["finalOrder"]
        assert isinstance(final_order, list)
        current = final_order.index(uncertain_region)
        other = 0 if current != 0 else len(final_order) - 1
        final_order[current], final_order[other] = final_order[other], final_order[current]
    else:
        page, diagnostics, trusted = _producer_case(
            tmp_path=tmp_path,
            monkeypatch=monkeypatch,
            slice_name="b1-horizontal",
            arms=(ArmId.B1_ONLY, ArmId.C1_C2_C3_B1),
            panels=(PanelBox(0, 0, 120, 120), PanelBox(200, 0, 320, 120)),
            regions=(
                (20, 20, 40, 40),
                (60, 50, 80, 70),
                (220, 20, 240, 40),
                (260, 50, 280, 70),
            ),
            pair=("r1", "r2"),
        )
        target = diagnostics[ArmId.B1_ONLY]["Q901"]
        blocks = _materialized_blocks(target)
        assert len(blocks) == 2
        first, second = blocks
        assert len(first) == len(second) == 2
        if mutation == "interleaved-groups":
            target["finalOrder"] = [first[0], second[0], first[1], second[1]]
        elif mutation == "inverted-group-blocks":
            target["finalOrder"] = second + first
        else:
            target["finalOrder"] = [first[0], second[0], second[1], first[1]]

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment=expected_fragment,
    )


def test_v3_relation_panel_endpoint_cannot_reference_empty_group(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-gutter-bridge",
        arms=(ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1),
        panels=(
            PanelBox(0, 0, 100, 100),
            PanelBox(200, 0, 300, 100),
            PanelBox(600, 0, 700, 100),
        ),
        regions=((20, 20, 40, 40), (220, 20, 240, 40), (140, 40, 160, 60)),
        pair=("r2", "r3"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    edges = target["relationEdges"]
    assert isinstance(edges, list)
    assert edges
    edge = edges[0]
    assert isinstance(edge, dict)
    if str(edge["sourceNode"]).startswith("g"):
        edge["sourceNode"] = "g002"
    else:
        edge["targetNode"] = "g002"

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="relation panel endpoint must reference an active occupied group",
    )


def test_v3_overlap_bracket_endpoints_equal_assignment_candidate_groups(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-ambiguous-overlap-bridge",
        arms=(ArmId.C2_ONLY, ArmId.C1_C2, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1),
        panels=(
            PanelBox(0, 0, 120, 100),
            PanelBox(100, 0, 220, 100),
            PanelBox(0, 300, 120, 400),
        ),
        regions=(
            (20, 20, 40, 40),
            (180, 20, 200, 40),
            (105, 40, 115, 60),
            (20, 320, 40, 340),
        ),
        pair=("r2", "r3"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    edges = target["relationEdges"]
    assert isinstance(edges, list)
    assert len(edges) == 2
    edge = edges[1]
    assert isinstance(edge, dict)
    if str(edge["sourceNode"]).startswith("g"):
        edge["sourceNode"] = "g002"
    else:
        edge["targetNode"] = "g002"

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="overlap bridge panel endpoints must equal assignment candidate groups",
    )


def test_v3_generic_non_unique_slot_claim_fails_producer_authentication(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-one-sided-non-unique-fail-closed",
        arms=(ArmId.C2_ONLY,),
        panels=(
            PanelBox(0, 0, 100, 100),
            PanelBox(0, 200, 100, 300),
            PanelBox(0, 400, 100, 500),
        ),
        regions=(
            (20, 20, 40, 40),
            (20, 220, 40, 240),
            (20, 420, 40, 440),
            (150, 120, 250, 380),
        ),
        pair=("r1", "r4"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    assignments = target["assignments"]
    assert isinstance(assignments, list)
    uncertain = next(
        item
        for item in assignments
        if isinstance(item, dict) and isinstance(item.get("uncertainNodeLabel"), str)
    )
    label = str(uncertain["uncertainNodeLabel"])
    target["relationEdges"] = [
        {
            "sourceNode": "g000",
            "targetNode": label,
            "rule": "uncertain-aligned-top-before-bottom",
        },
        {
            "sourceNode": label,
            "targetNode": "g002",
            "rule": "uncertain-aligned-top-before-bottom",
        },
    ]

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="relationEdges: does not match frozen producer recomputation",
    )


def test_v3_uncertain_relation_conflict_requires_uncertain_assignment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-conflict-cycle-safety",
        arms=(ArmId.CONTROL, ArmId.C2_ONLY),
        panels=(PanelBox(0, 0, 100, 100), PanelBox(200, 0, 300, 100)),
        regions=((20, 20, 40, 40), (220, 20, 240, 40)),
        pair=("r1", "r2"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    target["fallbackReason"] = "uncertain-relation-conflict"
    target["usedPanelEvidence"] = False
    target["nodeOrder"] = []
    target["relationEdges"] = []
    target["finalOrder"] = list(target["fallbackOrder"])

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="uncertain relation conflict requires an uncertain assignment",
    )


def test_v3_region_id_source_index_is_bound_to_trusted_arm_page_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-one-sided-non-unique-fail-closed",
        arms=(ArmId.C2_ONLY,),
        panels=(PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300)),
        regions=((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450)),
        pair=("r1", "r3"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    assignments = target["assignments"]
    assert isinstance(assignments, list)
    first, second = assignments[0], assignments[1]
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    first["regionId"], second["regionId"] = second["regionId"], first["regionId"]

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="regionId/sourceIndex must match trusted ArmPageInput position",
    )


def test_v3_geometry_dependent_relation_claim_uses_frozen_producer_recomputation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-one-sided-non-unique-fail-closed",
        arms=(ArmId.C2_ONLY,),
        panels=(PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300)),
        regions=((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450)),
        pair=("r1", "r3"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    assignments = target["assignments"]
    assert isinstance(assignments, list)
    uncertain = next(
        item
        for item in assignments
        if isinstance(item, dict) and isinstance(item.get("uncertainNodeLabel"), str)
    )
    label = str(uncertain["uncertainNodeLabel"])
    node_order = target["nodeOrder"]
    assert isinstance(node_order, list)
    group_nodes = [node for node in node_order if str(node).startswith("g")]
    assert len(group_nodes) >= 2
    target["relationEdges"] = [
        {
            "sourceNode": str(group_nodes[0]),
            "targetNode": label,
            "rule": "uncertain-aligned-top-before-bottom",
        },
        {
            "sourceNode": label,
            "targetNode": str(group_nodes[-1]),
            "rule": "uncertain-aligned-top-before-bottom",
        },
    ]

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="relationEdges: does not match frozen producer recomputation",
    )


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("box-count-mismatch", "boxCount must equal len(boxes)"),
        ("missing-node-order", "field set mismatch"),
        ("invalid-region-integrity", "production serializer requires true"),
        ("relation-endpoint-outside-vocabulary", "relation endpoint outside node vocabulary"),
    ],
)
def test_v3_prior_production_shape_regressions_remain_fail_closed(
    mutation: str,
    expected_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-one-sided-non-unique-fail-closed",
        arms=(ArmId.C2_ONLY,),
        panels=(PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300)),
        regions=((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450)),
        pair=("r1", "r3"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    if mutation == "box-count-mismatch":
        pre = target["preSegmentation"]
        assert isinstance(pre, dict)
        pre["boxCount"] = 1
    elif mutation == "missing-node-order":
        del target["nodeOrder"]
    elif mutation == "invalid-region-integrity":
        integrity = target["regionIntegrity"]
        assert isinstance(integrity, dict)
        integrity["countPreserved"] = False
    else:
        assignments = target["assignments"]
        assert isinstance(assignments, list)
        uncertain = next(
            item
            for item in assignments
            if isinstance(item, dict) and isinstance(item.get("uncertainNodeLabel"), str)
        )
        target["relationEdges"] = [
            {
                "sourceNode": "bogus",
                "targetNode": str(uncertain["uncertainNodeLabel"]),
                "rule": "uncertain-aligned-top-before-bottom",
            }
        ]

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment=expected_fragment,
    )


@pytest.mark.parametrize(
    ("pixel_case", "expected_fragment"),
    [
        ("not-ndarray", "trusted pixels must be a numpy ndarray"),
        ("wrong-dtype", "trusted pixels must use uint8 dtype"),
        ("wrong-height", "trusted pixels must use exact H x W x 3 RGB shape"),
        ("wrong-channels", "trusted pixels must use exact H x W x 3 RGB shape"),
    ],
)
def test_v3_trusted_pixels_require_canonical_runtime_representation(
    pixel_case: str,
    expected_fragment: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-one-sided-non-unique-fail-closed",
        arms=(ArmId.C2_ONLY,),
        panels=(PanelBox(0, 0, 100, 100), PanelBox(0, 200, 100, 300)),
        regions=((20, 20, 40, 40), (20, 220, 40, 240), (150, 350, 250, 450)),
        pair=("r1", "r3"),
    )
    original = trusted["Q901"]
    bad_pixels: Any
    if pixel_case == "not-ndarray":
        bad_pixels = object()
    elif pixel_case == "wrong-dtype":
        bad_pixels = original.pixels.astype(np.float32)
    elif pixel_case == "wrong-height":
        bad_pixels = original.pixels[:-1, :, :]
    else:
        bad_pixels = original.pixels[:, :, :2]
    trusted["Q901"] = V3TrustedPageInput(page=original.page, pixels=bad_pixels)

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment=expected_fragment,
    )


@pytest.mark.parametrize("malformed", [None, [], "diagnostics", 7])
def test_v3_malformed_top_level_container_is_classified_fail_closed(
    malformed: object,
) -> None:
    page = PageGroundTruth(
        page_id="Q901",
        reading_order=("r1", "r2"),
        unscored_region_ids=(),
        qualification_pairs=(
            QualificationPair(
                "p1", "r1", "r2", ("c2-one-sided-non-unique-fail-closed",)
            ),
        ),
        layout_tags=(),
    )
    _assert_invalid(
        page=page,
        diagnostics=malformed,
        trusted={},
        expected_fragment="top-level arm mapping object required",
    )


def test_v3_problem_order_for_multiple_uncertain_nodes_is_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page, diagnostics, trusted = _producer_case(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        slice_name="c2-one-sided-non-unique-fail-closed",
        arms=(ArmId.C2_ONLY,),
        panels=(PanelBox(0, 0, 100, 100), PanelBox(0, 300, 100, 400)),
        regions=(
            (20, 20, 40, 40),
            (20, 320, 40, 340),
            (150, 120, 180, 150),
            (150, 220, 180, 250),
        ),
        pair=("r1", "r3"),
    )
    target = diagnostics[ArmId.C2_ONLY]["Q901"]
    assignments = target["assignments"]
    assert isinstance(assignments, list)
    labels = sorted(
        str(item["uncertainNodeLabel"])
        for item in assignments
        if isinstance(item, dict) and isinstance(item.get("uncertainNodeLabel"), str)
    )
    assert len(labels) >= 2
    target["relationEdges"] = [
        {
            "sourceNode": "g000",
            "targetNode": label,
            "rule": "uncertain-aligned-top-before-bottom",
        }
        for label in reversed(labels)
    ]
    error = _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        trusted=trusted,
        expected_fragment="two-sided evidence",
    )
    two_sided = [
        problem for problem in error.problems if "two-sided evidence" in problem
    ]
    assert len(two_sided) == len(labels)
