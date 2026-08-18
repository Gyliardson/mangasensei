from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from scripts.reading_order_post_v2_qualification import DIAGNOSTIC_SCHEMA_VERSION
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import (
    EXERCISE_MINIMA_V3,
    V3DiagnosticValidationError,
    V3_INVALID_DIAGNOSTIC_HARNESS_STATUS,
    V3_INVALID_EXPERIMENT_CLASSIFICATION,
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
        reading_order=("r1", "r2"),
        unscored_region_ids=(),
        qualification_pairs=(QualificationPair("p1", "r1", "r2", (slice_name,)),),
        layout_tags=(),
    )


def _assignment(
    region_id: str,
    *,
    status: str,
    candidate_group_indices: list[int],
    assigned_group_index: int | None,
    uncertain_node_label: str | None,
) -> dict[str, object]:
    return {
        "regionId": region_id,
        "sourceIndex": int(region_id.removeprefix("r")) - 1,
        "candidateGroupIndices": candidate_group_indices,
        "status": status,
        "reason": "synthetic-evaluator-contract",
        "assignedGroupIndex": assigned_group_index,
        "uncertainNodeLabel": uncertain_node_label,
    }


def _default_diag(
    arm: ArmId, *, region_ids: tuple[str, ...] = ("r1", "r2")
) -> dict[str, object]:
    return {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "experimentArm": arm.value,
        "executionSha": EXECUTION_SHA,
        "pageId": "Q901",
        "preSegmentation": {
            "reliable": True,
            "reason": "reliable",
            "boxCount": 2,
            "boxes": [],
        },
        "segmentation": {"reliable": True, "reason": "reliable", "boxes": []},
        "recoveryReason": "disabled",
        "assignments": [],
        "relationEdges": [],
        "nodeOrder": [],
        "fallbackReason": None,
        "usedPanelEvidence": True,
        "fallbackOrder": list(region_ids),
        "finalOrder": list(region_ids),
        "regionDirections": {region_id: "v" for region_id in region_ids},
        "regionIntegrity": {
            "countPreserved": True,
            "objectIdentitySetPreserved": True,
            "contentConfidenceGeometryPreserved": True,
        },
    }


def _diagnostics() -> dict[ArmId, dict[str, dict[str, object]]]:
    return {arm: {"Q901": _default_diag(arm)} for arm in ArmId}


def _case(metric: str) -> tuple[str, dict[ArmId, dict[str, dict[str, object]]]]:
    diagnostics = _diagnostics()
    if metric == "c1_guarded_pairs":
        slice_name = "c1-boundary-positive"
        diagnostics[ArmId.CONTROL]["Q901"]["assignments"] = [
            _assignment(
                "r1",
                status="confident",
                candidate_group_indices=[0],
                assigned_group_index=0,
                uncertain_node_label=None,
            )
        ]
        diagnostics[ArmId.C1_ONLY]["Q901"]["assignments"] = [
            _assignment(
                "r1",
                status="unassigned",
                candidate_group_indices=[],
                assigned_group_index=None,
                uncertain_node_label="u000",
            )
        ]
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
                "uncertain-aligned-top-to-bottom",
            ),
        }
        slice_name, rule = details[metric]
        diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = [
            _assignment(
                "r1",
                status="unassigned",
                candidate_group_indices=[],
                assigned_group_index=None,
                uncertain_node_label="u000",
            )
        ]
        diagnostics[ArmId.C2_ONLY]["Q901"]["relationEdges"] = [
            {"sourceNode": "g000", "targetNode": "u000", "rule": rule}
        ]
    elif metric == "c2_fail_closed_no_relation_pairs":
        slice_name = "c2-one-sided-non-unique-fail-closed"
        diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = [
            _assignment(
                "r1",
                status="unassigned",
                candidate_group_indices=[],
                assigned_group_index=None,
                uncertain_node_label="u000",
            )
        ]
    elif metric == "c2_conflict_cycle_fallback_pairs":
        slice_name = "c2-conflict-cycle-safety"
        diagnostics[ArmId.C2_ONLY]["Q901"].update(
            {
                "fallbackReason": "uncertain-relation-conflict",
                "usedPanelEvidence": False,
            }
        )
    elif metric == "c3_positive_pairs":
        slice_name = "c3-positive-recovery"
        diagnostics[ArmId.C3_ONLY]["Q901"].update(
            {
                "preSegmentation": {
                    "reliable": False,
                    "reason": "fewer-than-two-groups",
                    "boxCount": 1,
                    "boxes": [],
                },
                "segmentation": {
                    "reliable": True,
                    "reason": "recovered-merged-frame",
                    "boxes": [],
                },
                "recoveryReason": "accepted-strong-anchor-plus-occlusion-supported-frame",
                "usedPanelEvidence": True,
            }
        )
    elif metric == "c3_rejection_pages":
        slice_name = "c3-invalid-topology-negative"
        diagnostics[ArmId.C3_ONLY]["Q901"].update(
            {
                "preSegmentation": {
                    "reliable": False,
                    "reason": "fewer-than-two-groups",
                    "boxCount": 1,
                    "boxes": [],
                },
                "segmentation": {
                    "reliable": False,
                    "reason": "fewer-than-two-groups",
                    "boxes": [],
                },
                "recoveryReason": "rejected-ambiguous-overlap",
                "assignments": [],
                "relationEdges": [],
                "usedPanelEvidence": False,
                "fallbackReason": "fewer-than-two-groups",
            }
        )
    elif metric in {"b1_horizontal_pairs", "b1_vertical_pairs", "b1_mixed_pairs"}:
        details = {
            "b1_horizontal_pairs": ("b1-horizontal", {"r1": "h", "r2": "h"}),
            "b1_vertical_pairs": ("b1-vertical", {"r1": "v", "r2": "v"}),
            "b1_mixed_pairs": ("b1-mixed-orientation", {"r1": "h", "r2": "v"}),
        }
        slice_name, directions = details[metric]
        diagnostics[ArmId.B1_ONLY]["Q901"].update(
            {
                "assignments": [
                    _assignment(
                        "r1",
                        status="confident",
                        candidate_group_indices=[0],
                        assigned_group_index=0,
                        uncertain_node_label=None,
                    ),
                    _assignment(
                        "r2",
                        status="confident",
                        candidate_group_indices=[0],
                        assigned_group_index=0,
                        uncertain_node_label=None,
                    ),
                ],
                "usedPanelEvidence": True,
                "regionDirections": directions,
            }
        )
    else:
        raise AssertionError(f"unknown metric: {metric}")
    return slice_name, diagnostics


def _break_case(metric: str, diagnostics: dict[ArmId, dict[str, dict[str, object]]]) -> None:
    if metric == "c1_guarded_pairs":
        diagnostics[ArmId.C1_ONLY]["Q901"]["assignments"] = diagnostics[ArmId.CONTROL][
            "Q901"
        ]["assignments"]
    elif metric in {"c2_gutter_pairs", "c2_overlap_pairs", "c2_pair_precedence_pairs"}:
        diagnostics[ArmId.C2_ONLY]["Q901"]["relationEdges"] = []
    elif metric == "c2_fail_closed_no_relation_pairs":
        diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = []
    elif metric == "c2_conflict_cycle_fallback_pairs":
        diagnostics[ArmId.C2_ONLY]["Q901"]["fallbackReason"] = None
    elif metric == "c3_positive_pairs":
        diagnostics[ArmId.C3_ONLY]["Q901"]["recoveryReason"] = (
            "rejected-no-unique-strong-frame-anchor"
        )
    elif metric == "c3_rejection_pages":
        diagnostics[ArmId.C3_ONLY]["Q901"]["recoveryReason"] = "not-eligible"
    elif metric in {"b1_horizontal_pairs", "b1_vertical_pairs", "b1_mixed_pairs"}:
        diagnostics[ArmId.B1_ONLY]["Q901"]["usedPanelEvidence"] = False
    else:
        raise AssertionError(f"unknown metric: {metric}")


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
    diagnostics = {
        arm: {"Q901": copy.deepcopy(pages["Q901"])} for arm, pages in diagnostics.items()
    }
    for pages in diagnostics.values():
        pages["Q901"]["fallbackOrder"] = ["r1", "r2", "r3"]
        pages["Q901"]["finalOrder"] = ["r1", "r2", "r3"]
        pages["Q901"]["regionDirections"] = {"r1": "v", "r2": "v", "r3": "v"}
    page = PageGroundTruth(
        page_id="Q901",
        reading_order=("r1", "r2", "r3"),
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
        ("missing-final-order", "missing required fields"),
        ("wrong-relation-type", "relationEdges: array required"),
        ("missing-arm", "required arm mapping missing/malformed"),
        ("missing-page", "required page missing"),
        ("wrong-arm-identity", "experimentArm: does not match arm key"),
        ("wrong-page-identity", "pageId: does not match annotation page"),
        ("inconsistent-orders", "fallbackOrder/finalOrder region sets differ"),
        ("inconsistent-assignment", "serializer invariant violated"),
    ],
)
def test_v3_invalid_diagnostic_is_classified_fail_closed(
    mutation: str, expected_fragment: str
) -> None:
    page = _page("c2-one-sided-non-unique-fail-closed")
    diagnostics = _diagnostics()
    diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = [
        _assignment(
            "r1",
            status="unassigned",
            candidate_group_indices=[],
            assigned_group_index=None,
            uncertain_node_label="u000",
        )
    ]
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
        target["finalOrder"] = ["r1"]
    elif mutation == "inconsistent-assignment":
        assignments = target["assignments"]
        assert isinstance(assignments, list)
        assignment = assignments[0]
        assert isinstance(assignment, dict)
        assignment["uncertainNodeLabel"] = None
    else:
        raise AssertionError(mutation)

    with pytest.raises(V3DiagnosticValidationError) as caught:
        build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert caught.value.classification == "INVALID_EXPERIMENT"
    assert caught.value.harness_status == "harness-invalid"
    assert expected_fragment in str(caught.value)


def test_v3_missing_c3_orders_cannot_satisfy_none_equals_none() -> None:
    page = _page("c3-invalid-topology-negative")
    _, diagnostics = _case("c3_rejection_pages")
    for arm in (ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1):
        diagnostics[arm]["Q901"].pop("finalOrder", None)
        diagnostics[arm]["Q901"].pop("fallbackOrder", None)
    with pytest.raises(V3DiagnosticValidationError):
        build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
