from __future__ import annotations

import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import (
    EXERCISE_MINIMA_V3,
    InvalidDiagnosticError,
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
    assigned_group_index: int | None,
    candidates: list[int],
    uncertain_node_label: str | None,
) -> dict[str, object]:
    return {
        "regionId": region_id,
        "sourceIndex": 0 if region_id == "r1" else 1,
        "candidateGroupIndices": candidates,
        "status": status,
        "reason": "synthetic-contract-test",
        "assignedGroupIndex": assigned_group_index,
        "uncertainNodeLabel": uncertain_node_label,
    }


def _default_diag() -> dict[str, object]:
    return {
        "schemaVersion": "reading-order-post-v2-diagnostic-v1",
        "experimentArm": "CONTROL",
        "executionSha": "0" * 40,
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
        "fallbackOrder": ["r1", "r2"],
        "finalOrder": ["r1", "r2"],
        "regionDirections": {"r1": "v", "r2": "v"},
        "regionIntegrity": {
            "countPreserved": True,
            "objectIdentitySetPreserved": True,
            "contentConfidenceGeometryPreserved": True,
        },
    }


def _diagnostics() -> dict[ArmId, dict[str, dict[str, object]]]:
    result = {arm: {"Q901": _default_diag()} for arm in ArmId}
    for arm, pages in result.items():
        pages["Q901"]["experimentArm"] = arm.value
    return result


def _case(metric: str) -> tuple[str, dict[ArmId, dict[str, dict[str, object]]]]:
    diagnostics = _diagnostics()
    if metric == "c1_guarded_pairs":
        slice_name = "c1-boundary-positive"
        diagnostics[ArmId.CONTROL]["Q901"]["assignments"] = [
            _assignment(
                "r1",
                status="confident",
                assigned_group_index=0,
                candidates=[0],
                uncertain_node_label=None,
            )
        ]
        diagnostics[ArmId.C1_ONLY]["Q901"]["assignments"] = [
            _assignment(
                "r1",
                status="unassigned",
                assigned_group_index=None,
                candidates=[],
                uncertain_node_label="u000",
            )
        ]
    elif metric in {"c2_gutter_pairs", "c2_overlap_pairs", "c2_pair_precedence_pairs"}:
        details = {
            "c2_gutter_pairs": ("c2-gutter-bridge", "unique-gutter-between-hard-panels"),
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
                assigned_group_index=None,
                candidates=[],
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
                assigned_group_index=None,
                candidates=[],
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
                "recoveryReason": "rejected-ambiguous-overlap",
                "assignments": [],
                "relationEdges": [],
                "usedPanelEvidence": False,
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
                        assigned_group_index=0,
                        candidates=[0],
                        uncertain_node_label=None,
                    ),
                    _assignment(
                        "r2",
                        status="confident",
                        assigned_group_index=0,
                        candidates=[0],
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
        diagnostics[ArmId.C2_ONLY]["Q901"]["usedPanelEvidence"] = True
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
    assert runtime["c3NegativeSemantics"] == "generic-rejection-page-witness"
    assert runtime["c3NegativeEvidenceUnit"] == "unique-page"
    assert runtime["positiveGateIndependence"]["dedicatedMechanismPagesRequired"] is True
    invalid = runtime["invalidDiagnosticSemantics"]
    assert invalid["futureRunnerStatus"] == "INVALID_EXPERIMENT"
    assert invalid["classification"] == "harness-invalid"
    assert invalid["evaluatorBehavior"] == "raise-before-v2-delegation"


@pytest.mark.parametrize("metric", tuple(EXERCISE_MINIMA_V3))
def test_v3_evaluator_counts_true_state_and_rejects_nearby_mismatch(metric: str) -> None:
    slice_name, diagnostics = _case(metric)
    page = _page(slice_name)
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts[metric].count == 1

    _break_case(metric, diagnostics)
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts[metric].count == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-final-order", "missing required fields"),
        ("wrong-relation-edges-type", "relationEdges: list required"),
        ("malformed-assignment", "candidateGroupIndices: required"),
        ("inconsistent-fallback", "fallback evidence/order inconsistent"),
        ("missing-arm", "missing arm C2_ONLY"),
        ("missing-page", "missing page Q901"),
    ],
)
def test_v3_invalid_diagnostic_is_harness_invalid_and_never_counts(
    mutation: str,
    message: str,
) -> None:
    slice_name, diagnostics = _case("c2_fail_closed_no_relation_pairs")
    if mutation == "missing-final-order":
        del diagnostics[ArmId.C2_ONLY]["Q901"]["finalOrder"]
    elif mutation == "wrong-relation-edges-type":
        diagnostics[ArmId.C2_ONLY]["Q901"]["relationEdges"] = {}
    elif mutation == "malformed-assignment":
        assignment = diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"]
        assert isinstance(assignment, list) and isinstance(assignment[0], dict)
        del assignment[0]["candidateGroupIndices"]
    elif mutation == "inconsistent-fallback":
        diagnostics[ArmId.C2_ONLY]["Q901"]["usedPanelEvidence"] = False
        diagnostics[ArmId.C2_ONLY]["Q901"]["finalOrder"] = ["r2", "r1"]
    elif mutation == "missing-arm":
        del diagnostics[ArmId.C2_ONLY]
    elif mutation == "missing-page":
        del diagnostics[ArmId.C2_ONLY]["Q901"]
    else:
        raise AssertionError(mutation)

    with pytest.raises(InvalidDiagnosticError, match=message):
        build_exercise_report_v3(annotations=(_page(slice_name),), diagnostics=diagnostics)


def test_v3_c3_missing_both_orders_cannot_false_positive() -> None:
    slice_name, diagnostics = _case("c3_rejection_pages")
    del diagnostics[ArmId.C3_ONLY]["Q901"]["finalOrder"]
    del diagnostics[ArmId.C3_ONLY]["Q901"]["fallbackOrder"]
    with pytest.raises(InvalidDiagnosticError, match="missing required fields"):
        build_exercise_report_v3(annotations=(_page(slice_name),), diagnostics=diagnostics)


def test_v3_c3_page_witness_cannot_multiply_across_pairs() -> None:
    _, diagnostics = _case("c3_rejection_pages")
    page = PageGroundTruth(
        page_id="Q901",
        reading_order=("r1", "r2"),
        unscored_region_ids=(),
        qualification_pairs=(
            QualificationPair("p1", "r1", "r2", ("c3-zero-multiple-anchor-negative",)),
            QualificationPair("p2", "r1", "r2", ("c3-invalid-topology-negative",)),
            QualificationPair("p3", "r1", "r2", ("c3-insufficient-visible-support-negative",)),
        ),
        layout_tags=(),
    )
    report = build_exercise_report_v3(annotations=(page,), diagnostics=deepcopy(diagnostics))
    count = report.counts["c3_rejection_pages"]
    assert count.count == 1
    assert count.page_ids == ("Q901",)
    assert count.pair_ids == ()
