from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts.reading_order_post_v2_qualification import DIAGNOSTIC_SCHEMA_VERSION
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import (
    EXERCISE_MINIMA_V3,
    V3_INVALID_DIAGNOSTIC_HARNESS_STATUS,
    V3_INVALID_EXPERIMENT_CLASSIFICATION,
    V3DiagnosticValidationError,
    build_exercise_report_v3,
)

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
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "reading-order-post-v2-qualification.yml"
V3_EXPERIMENT_ID = "reading-order-post-v2-c1-c2-c3-b1-v3"
EXECUTION_SHA = "f45facb2284d740df2f294800f705414e0ba465e"
REGION_IDS = ("r1", "r2", "r3")
BASE_BOXES = (
    {"x1": 0, "y1": 0, "x2": 120, "y2": 120},
    {"x1": 200, "y1": 200, "x2": 320, "y2": 320},
)
MERGED_BOX = {"x1": 0, "y1": 0, "x2": 320, "y2": 320}


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


def _page(slice_name: str) -> PageGroundTruth:
    return PageGroundTruth(
        page_id="Q901",
        reading_order=REGION_IDS,
        unscored_region_ids=(),
        qualification_pairs=(QualificationPair("p1", "r1", "r2", (slice_name,)),),
        layout_tags=(),
    )


def _assignment(
    arm: ArmId,
    region_id: str,
    *,
    status: str,
    candidate_group_indices: list[int],
    assigned_group_index: int | None,
) -> dict[str, object]:
    source_index = int(region_id.removeprefix("r")) - 1
    guarded = arm.c1
    if status == "confident":
        reason = (
            "unique-guarded-center-containment"
            if guarded
            else "unique-center-containment"
        )
    elif status == "ambiguous":
        reason = (
            "multiple-guarded-center-containment"
            if guarded
            else "multiple-center-containment"
        )
    elif status == "unassigned":
        reason = (
            "no-guarded-center-containment"
            if guarded
            else "no-center-containment"
        )
    else:
        raise AssertionError(status)
    return {
        "regionId": region_id,
        "sourceIndex": source_index,
        "candidateGroupIndices": candidate_group_indices,
        "status": status,
        "reason": reason,
        "assignedGroupIndex": assigned_group_index,
        "uncertainNodeLabel": (
            f"u{source_index:03d}" if assigned_group_index is None else None
        ),
    }


def _baseline_assignments(arm: ArmId) -> list[dict[str, object]]:
    return [
        _assignment(
            arm,
            "r1",
            status="confident",
            candidate_group_indices=[0],
            assigned_group_index=0,
        ),
        _assignment(
            arm,
            "r2",
            status="confident",
            candidate_group_indices=[0],
            assigned_group_index=0,
        ),
        _assignment(
            arm,
            "r3",
            status="confident",
            candidate_group_indices=[1],
            assigned_group_index=1,
        ),
    ]


def _uncertain_r1_assignments(arm: ArmId) -> list[dict[str, object]]:
    assignments = _baseline_assignments(arm)
    assignments[0] = _assignment(
        arm,
        "r1",
        status="unassigned",
        candidate_group_indices=[],
        assigned_group_index=None,
    )
    return assignments


def _ambiguous_r1_assignments(arm: ArmId) -> list[dict[str, object]]:
    assignments = _baseline_assignments(arm)
    assignments[0] = _assignment(
        arm,
        "r1",
        status="ambiguous",
        candidate_group_indices=[0, 1],
        assigned_group_index=None,
    )
    return assignments


def _default_diag(arm: ArmId) -> dict[str, object]:
    boxes = [dict(box) for box in BASE_BOXES]
    return {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "experimentArm": arm.value,
        "executionSha": EXECUTION_SHA,
        "pageId": "Q901",
        "preSegmentation": {
            "reliable": True,
            "reason": "reliable",
            "boxCount": len(boxes),
            "boxes": copy.deepcopy(boxes),
        },
        "segmentation": {
            "reliable": True,
            "reason": "reliable",
            "boxes": copy.deepcopy(boxes),
        },
        "recoveryReason": "not-needed" if arm.c3 else "disabled",
        "assignments": _baseline_assignments(arm),
        "relationEdges": [],
        "nodeOrder": ["g000", "g001"],
        "fallbackReason": None,
        "usedPanelEvidence": True,
        "fallbackOrder": list(REGION_IDS),
        "finalOrder": list(REGION_IDS),
        "regionDirections": {region_id: "h" for region_id in REGION_IDS},
        "regionIntegrity": {
            "countPreserved": True,
            "objectIdentitySetPreserved": True,
            "contentConfidenceGeometryPreserved": True,
        },
    }


def _diagnostics() -> dict[ArmId, dict[str, dict[str, object]]]:
    return {arm: {"Q901": _default_diag(arm)} for arm in ArmId}


def _set_uncertain_success(
    diagnostic: dict[str, object],
    arm: ArmId,
    *,
    edges: list[dict[str, str]] | None = None,
    ambiguous: bool = False,
) -> None:
    diagnostic["assignments"] = (
        _ambiguous_r1_assignments(arm)
        if ambiguous
        else _uncertain_r1_assignments(arm)
    )
    diagnostic["relationEdges"] = [] if edges is None else edges
    diagnostic["nodeOrder"] = ["g000", "u000", "g001"]


def _set_c3_rejection(diagnostic: dict[str, object]) -> None:
    diagnostic.update(
        {
            "preSegmentation": {
                "reliable": False,
                "reason": "fewer-than-two-groups",
                "boxCount": 1,
                "boxes": [dict(MERGED_BOX)],
            },
            "segmentation": {
                "reliable": False,
                "reason": "fewer-than-two-groups",
                "boxes": [dict(MERGED_BOX)],
            },
            "recoveryReason": "rejected-ambiguous-overlap",
            "assignments": [],
            "relationEdges": [],
            "nodeOrder": [],
            "fallbackReason": "fewer-than-two-groups",
            "usedPanelEvidence": False,
            "finalOrder": list(REGION_IDS),
            "fallbackOrder": list(REGION_IDS),
        }
    )


def _set_c3_positive(diagnostic: dict[str, object], arm: ArmId) -> None:
    diagnostic.update(
        {
            "preSegmentation": {
                "reliable": False,
                "reason": "fewer-than-two-groups",
                "boxCount": 1,
                "boxes": [dict(MERGED_BOX)],
            },
            "segmentation": {
                "reliable": True,
                "reason": "recovered-merged-frame",
                "boxes": [dict(box) for box in BASE_BOXES],
            },
            "recoveryReason": "accepted-strong-anchor-plus-occlusion-supported-frame",
            "assignments": _baseline_assignments(arm),
            "relationEdges": [],
            "nodeOrder": ["g000", "g001"],
            "fallbackReason": None,
            "usedPanelEvidence": True,
        }
    )


def _set_b1_directions(
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
    directions: dict[str, str],
) -> None:
    for arm in (ArmId.B1_ONLY, ArmId.C1_C2_C3_B1):
        diagnostics[arm]["Q901"]["regionDirections"] = dict(directions)


def _case(metric: str) -> tuple[str, dict[ArmId, dict[str, dict[str, object]]]]:
    diagnostics = _diagnostics()
    if metric == "c1_guarded_pairs":
        slice_name = "c1-boundary-positive"
        _set_uncertain_success(
            diagnostics[ArmId.C1_ONLY]["Q901"],
            ArmId.C1_ONLY,
        )
    elif metric in {"c2_gutter_pairs", "c2_overlap_pairs", "c2_pair_precedence_pairs"}:
        details = {
            "c2_gutter_pairs": (
                "c2-gutter-bridge",
                "unique-gutter-between-hard-panels",
            ),
            "c2_overlap_pairs": (
                "c2-ambiguous-overlap-bridge",
                "validated-overlap-bridge-right-before-left",
            ),
            "c2_pair_precedence_pairs": (
                "c2-pair-precedence-slot",
                "uncertain-aligned-top-before-bottom",
            ),
        }
        slice_name, rule = details[metric]
        _set_uncertain_success(
            diagnostics[ArmId.C2_ONLY]["Q901"],
            ArmId.C2_ONLY,
            edges=[
                {"sourceNode": "g000", "targetNode": "u000", "rule": rule},
                {"sourceNode": "u000", "targetNode": "g001", "rule": rule},
            ],
            ambiguous=metric == "c2_overlap_pairs",
        )
    elif metric == "c2_fail_closed_no_relation_pairs":
        slice_name = "c2-one-sided-non-unique-fail-closed"
        _set_uncertain_success(
            diagnostics[ArmId.C2_ONLY]["Q901"],
            ArmId.C2_ONLY,
        )
    elif metric == "c2_conflict_cycle_fallback_pairs":
        slice_name = "c2-conflict-cycle-safety"
        target = diagnostics[ArmId.C2_ONLY]["Q901"]
        target.update(
            {
                "assignments": _uncertain_r1_assignments(ArmId.C2_ONLY),
                "relationEdges": [],
                "nodeOrder": [],
                "fallbackReason": "uncertain-relation-conflict",
                "usedPanelEvidence": False,
                "finalOrder": list(REGION_IDS),
                "fallbackOrder": list(REGION_IDS),
            }
        )
    elif metric == "c3_positive_pairs":
        slice_name = "c3-positive-recovery"
        for arm in (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1):
            _set_c3_positive(diagnostics[arm]["Q901"], arm)
    elif metric == "c3_rejection_pages":
        slice_name = "c3-invalid-topology-negative"
        for arm in (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1):
            _set_c3_rejection(diagnostics[arm]["Q901"])
    elif metric in {"b1_horizontal_pairs", "b1_vertical_pairs", "b1_mixed_pairs"}:
        details = {
            "b1_horizontal_pairs": ("b1-horizontal", {"r1": "h", "r2": "h", "r3": "h"}),
            "b1_vertical_pairs": ("b1-vertical", {"r1": "v", "r2": "v", "r3": "h"}),
            "b1_mixed_pairs": (
                "b1-mixed-orientation",
                {"r1": "h", "r2": "v", "r3": "h"},
            ),
        }
        slice_name, directions = details[metric]
        _set_b1_directions(diagnostics, directions)
    else:
        raise AssertionError(f"unknown metric: {metric}")
    return slice_name, diagnostics


def _break_case(metric: str, diagnostics: dict[ArmId, dict[str, dict[str, object]]]) -> None:
    if metric == "c1_guarded_pairs":
        diagnostics[ArmId.C1_ONLY]["Q901"] = _default_diag(ArmId.C1_ONLY)
    elif metric in {"c2_gutter_pairs", "c2_overlap_pairs", "c2_pair_precedence_pairs"}:
        diagnostics[ArmId.C2_ONLY]["Q901"]["relationEdges"] = []
    elif metric == "c2_fail_closed_no_relation_pairs":
        diagnostics[ArmId.C2_ONLY]["Q901"] = _default_diag(ArmId.C2_ONLY)
    elif metric == "c2_conflict_cycle_fallback_pairs":
        diagnostics[ArmId.C2_ONLY]["Q901"] = _default_diag(ArmId.C2_ONLY)
    elif metric in {"c3_positive_pairs", "c3_rejection_pages"}:
        for arm in (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1):
            diagnostics[arm]["Q901"] = _default_diag(arm)
    elif metric in {"b1_horizontal_pairs", "b1_vertical_pairs"}:
        _set_b1_directions(
            diagnostics,
            {"r1": "h", "r2": "v", "r3": "h"},
        )
    elif metric == "b1_mixed_pairs":
        _set_b1_directions(
            diagnostics,
            {"r1": "h", "r2": "h", "r3": "h"},
        )
    else:
        raise AssertionError(f"unknown metric: {metric}")


def _assert_invalid(
    *,
    page: PageGroundTruth,
    diagnostics: Any,
    expected_fragment: str,
) -> None:
    with pytest.raises(V3DiagnosticValidationError) as caught:
        build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert caught.value.classification == V3_INVALID_EXPERIMENT_CLASSIFICATION
    assert caught.value.harness_status == V3_INVALID_DIAGNOSTIC_HARNESS_STATUS
    assert expected_fragment in str(caught.value)


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
    production = runtime["productionDiagnosticContract"]
    assert production["serializationReference"].endswith("/run_arm.py")
    assert production["preSegmentation"]["boxCountEqualsSerializedBoxesLength"] is True
    assert production["regionSet"]["allSerializedRegionViewsUseFullPageSet"] is True
    assert production["regionIntegrity"]["allFlagsMustBeTrue"] is True
    assert production["nodeGraph"]["relationEndpointsMustBelongToNodeVocabulary"] is True
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


@pytest.mark.parametrize("metric", tuple(EXERCISE_MINIMA_V3))
def test_v3_evaluator_counts_true_state_and_rejects_nearby_mismatch(metric: str) -> None:
    slice_name, diagnostics = _case(metric)
    page = _page(slice_name)
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts[metric].count == 1

    _break_case(metric, diagnostics)
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts[metric].count == 0


def test_v3_c3_rejection_counts_unique_page_witness_not_pairs() -> None:
    _, diagnostics = _case("c3_rejection_pages")
    page = PageGroundTruth(
        page_id="Q901",
        reading_order=REGION_IDS,
        unscored_region_ids=(),
        qualification_pairs=(
            QualificationPair("p1", "r1", "r2", ("c3-zero-multiple-anchor-negative",)),
            QualificationPair("p2", "r2", "r3", ("c3-invalid-topology-negative",)),
        ),
        layout_tags=(),
    )
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    count = report.counts["c3_rejection_pages"]
    assert count.count == 1
    assert count.page_ids == ("Q901",)
    assert count.pair_ids == ()


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("missing-final-order", "field set mismatch"),
        ("wrong-relation-type", "relationEdges: array required"),
        ("missing-arm", "required arm mapping missing/malformed"),
        ("missing-page", "required page missing"),
        ("wrong-arm-identity", "experimentArm: does not match arm key"),
        ("wrong-page-identity", "pageId: does not match annotation page"),
        ("inconsistent-orders", "exact permutation of full page region set"),
        ("box-count-mismatch", "boxCount must equal len(boxes)"),
        ("missing-pre-boxes", "field set mismatch"),
        ("missing-segmentation-boxes", "field set mismatch"),
        ("malformed-box", "integer x1/y1/x2/y2 required"),
        ("missing-node-order", "field set mismatch"),
        ("invalid-node-order", "success nodeOrder must cover exact materialized node vocabulary"),
        ("relation-endpoint-outside-vocabulary", "relation endpoint outside node vocabulary"),
        ("relation-one-sided", "two-sided evidence"),
        ("overlap-with-non-ambiguous-assignment", "two-candidate ambiguous assignment"),
        ("assignment-impossible", "confident assignment requires exactly one candidate"),
        ("assignment-index-outside-groups", "index outside segmentation boxes"),
        ("invalid-direction", "production fixture direction must be h or v"),
        ("inconsistent-region-set", "exact permutation of full page region set"),
        ("missing-region-integrity", "field set mismatch"),
        ("invalid-region-integrity", "production serializer requires true"),
        ("invalid-recovery-reason", "unsupported eligible C3 recovery reason"),
    ],
)
def test_v3_production_impossible_diagnostic_is_classified_fail_closed(
    mutation: str, expected_fragment: str
) -> None:
    if mutation in {
        "relation-endpoint-outside-vocabulary",
        "relation-one-sided",
    }:
        page = _page("c2-pair-precedence-slot")
        _, diagnostics = _case("c2_pair_precedence_pairs")
        target = diagnostics[ArmId.C2_ONLY]["Q901"]
    elif mutation == "overlap-with-non-ambiguous-assignment":
        page = _page("c2-ambiguous-overlap-bridge")
        _, diagnostics = _case("c2_overlap_pairs")
        target = diagnostics[ArmId.C2_ONLY]["Q901"]
    elif mutation == "invalid-recovery-reason":
        page = _page("c3-invalid-topology-negative")
        _, diagnostics = _case("c3_rejection_pages")
        target = diagnostics[ArmId.C3_ONLY]["Q901"]
    else:
        page = _page("c2-one-sided-non-unique-fail-closed")
        _, diagnostics = _case("c2_fail_closed_no_relation_pairs")
        target = diagnostics[ArmId.C2_ONLY]["Q901"]

    if mutation == "missing-final-order":
        del target["finalOrder"]
    elif mutation == "wrong-relation-type":
        target["relationEdges"] = {}
    elif mutation == "missing-arm":
        del diagnostics[ArmId.C2_ONLY]
    elif mutation == "missing-page":
        del diagnostics[ArmId.C2_ONLY]["Q901"]
    elif mutation == "wrong-arm-identity":
        target["experimentArm"] = ArmId.CONTROL.value
    elif mutation == "wrong-page-identity":
        target["pageId"] = "Q902"
    elif mutation == "inconsistent-orders":
        target["fallbackOrder"] = ["r3", "r2"]
    elif mutation == "box-count-mismatch":
        pre = target["preSegmentation"]
        assert isinstance(pre, dict)
        pre["boxCount"] = 1
    elif mutation == "missing-pre-boxes":
        pre = target["preSegmentation"]
        assert isinstance(pre, dict)
        del pre["boxes"]
    elif mutation == "missing-segmentation-boxes":
        segmentation = target["segmentation"]
        assert isinstance(segmentation, dict)
        del segmentation["boxes"]
    elif mutation == "malformed-box":
        pre = target["preSegmentation"]
        assert isinstance(pre, dict)
        boxes = pre["boxes"]
        assert isinstance(boxes, list)
        boxes[0] = {"x1": "0", "y1": 0, "x2": 120, "y2": 120}
    elif mutation == "missing-node-order":
        del target["nodeOrder"]
    elif mutation == "invalid-node-order":
        target["nodeOrder"] = ["g000"]
    elif mutation == "relation-endpoint-outside-vocabulary":
        edges = target["relationEdges"]
        assert isinstance(edges, list)
        edge = edges[0]
        assert isinstance(edge, dict)
        edge["sourceNode"] = "bogus"
    elif mutation == "relation-one-sided":
        edges = target["relationEdges"]
        assert isinstance(edges, list)
        target["relationEdges"] = edges[:1]
    elif mutation == "overlap-with-non-ambiguous-assignment":
        target["assignments"] = _uncertain_r1_assignments(ArmId.C2_ONLY)
    elif mutation == "assignment-impossible":
        assignments = target["assignments"]
        assert isinstance(assignments, list)
        assignment = assignments[0]
        assert isinstance(assignment, dict)
        assignment["status"] = "confident"
        assignment["reason"] = "unique-center-containment"
        assignment["candidateGroupIndices"] = [0, 1]
        assignment["assignedGroupIndex"] = 0
        assignment["uncertainNodeLabel"] = None
    elif mutation == "assignment-index-outside-groups":
        assignments = target["assignments"]
        assert isinstance(assignments, list)
        assignment = assignments[0]
        assert isinstance(assignment, dict)
        assignment["candidateGroupIndices"] = [2]
    elif mutation == "invalid-direction":
        directions = target["regionDirections"]
        assert isinstance(directions, dict)
        directions["r1"] = "auto"
    elif mutation == "inconsistent-region-set":
        target["finalOrder"] = ["r1", "r2"]
    elif mutation == "missing-region-integrity":
        del target["regionIntegrity"]
    elif mutation == "invalid-region-integrity":
        integrity = target["regionIntegrity"]
        assert isinstance(integrity, dict)
        integrity["countPreserved"] = False
    elif mutation == "invalid-recovery-reason":
        target["recoveryReason"] = "rejected-not-a-production-reason"
    else:
        raise AssertionError(mutation)

    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        expected_fragment=expected_fragment,
    )


@pytest.mark.parametrize("malformed", [None, [], "diagnostics", 7])
def test_v3_malformed_top_level_container_is_classified_fail_closed(malformed: object) -> None:
    _assert_invalid(
        page=_page("c2-one-sided-non-unique-fail-closed"),
        diagnostics=malformed,
        expected_fragment="top-level arm mapping object required",
    )


def test_v3_missing_c3_orders_cannot_satisfy_none_equals_none() -> None:
    page = _page("c3-invalid-topology-negative")
    _, diagnostics = _case("c3_rejection_pages")
    for arm in (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1):
        diagnostics[arm]["Q901"].pop("finalOrder", None)
        diagnostics[arm]["Q901"].pop("fallbackOrder", None)
    _assert_invalid(
        page=page,
        diagnostics=diagnostics,
        expected_fragment="field set mismatch",
    )
