from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Callable

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
EXECUTION_SHA = "0" * 40


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


def _box(x1: int, y1: int, x2: int, y2: int) -> dict[str, int]:
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


def _page(slice_name: str) -> PageGroundTruth:
    return PageGroundTruth(
        page_id="Q901",
        reading_order=("r1", "r2"),
        unscored_region_ids=(),
        qualification_pairs=(QualificationPair("p1", "r1", "r2", (slice_name,)),),
        layout_tags=(),
    )


def _default_diag(
    arm: ArmId,
    *,
    page_id: str = "Q901",
    region_ids: tuple[str, ...] = ("r1", "r2"),
) -> dict[str, object]:
    boxes = [_box(0, 0, 100, 100), _box(200, 0, 300, 100)]
    return {
        "schemaVersion": "reading-order-post-v2-diagnostic-v1",
        "experimentArm": arm.value,
        "executionSha": EXECUTION_SHA,
        "pageId": page_id,
        "preSegmentation": {
            "reliable": True,
            "reason": "reliable",
            "boxCount": 2,
            "boxes": boxes,
        },
        "segmentation": {"reliable": True, "reason": "reliable", "boxes": boxes},
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


def _diagnostics(
    *,
    region_ids: tuple[str, ...] = ("r1", "r2"),
) -> dict[ArmId, dict[str, dict[str, object]]]:
    return {
        arm: {"Q901": _default_diag(arm, region_ids=region_ids)}
        for arm in ArmId
    }


def _unassigned(region_id: str, source_index: int, label: str) -> dict[str, object]:
    return {
        "regionId": region_id,
        "sourceIndex": source_index,
        "candidateGroupIndices": [],
        "status": "unassigned",
        "reason": "outside-all-groups",
        "assignedGroupIndex": None,
        "uncertainNodeLabel": label,
    }


def _confident(region_id: str, source_index: int, group_index: int) -> dict[str, object]:
    return {
        "regionId": region_id,
        "sourceIndex": source_index,
        "candidateGroupIndices": [group_index],
        "status": "confident",
        "reason": "inside-unique-group",
        "assignedGroupIndex": group_index,
        "uncertainNodeLabel": None,
    }


def _case(metric: str) -> tuple[str, dict[ArmId, dict[str, dict[str, object]]]]:
    diagnostics = _diagnostics()
    if metric == "c1_guarded_pairs":
        slice_name = "c1-boundary-positive"
        diagnostics[ArmId.CONTROL]["Q901"]["assignments"] = [_confident("r1", 0, 0)]
        diagnostics[ArmId.C1_ONLY]["Q901"]["assignments"] = [_unassigned("r1", 0, "u000")]
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
        diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = [_unassigned("r1", 0, "u000")]
        diagnostics[ArmId.C2_ONLY]["Q901"]["relationEdges"] = [
            {"sourceNode": "g000", "targetNode": "u000", "rule": rule}
        ]
    elif metric == "c2_fail_closed_no_relation_pairs":
        slice_name = "c2-one-sided-non-unique-fail-closed"
        diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = [_unassigned("r1", 0, "u000")]
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
                    "boxes": [_box(100, 100, 900, 900)],
                },
                "segmentation": {
                    "reliable": True,
                    "reason": "recovered-merged-frame",
                    "boxes": [_box(100, 100, 500, 500), _box(400, 300, 700, 700)],
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
                    "boxes": [_box(100, 100, 900, 900)],
                },
                "segmentation": {
                    "reliable": False,
                    "reason": "fewer-than-two-groups",
                    "boxes": [_box(100, 100, 900, 900)],
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
                "assignments": [_confident("r1", 0, 0), _confident("r2", 1, 0)],
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
    assert base["gitBlobSha"] == _git_blob(
        "scripts/reading_order_post_v2_qualification/spec/experiment-spec-v2.json"
    )
    candidate = methodology["candidateBinding"]
    assert candidate["gitBlobSha"] == _git_blob(candidate["path"])
    runtime = methodology["runtimeReachability"]
    assert runtime["exerciseMinima"] == EXERCISE_MINIMA_V3
    assert runtime["evaluatorGitBlobSha"] == _git_blob(runtime["evaluatorPath"])
    calibration = runtime["calibrationSet"]
    assert calibration["candidateReachabilityTestGitBlobSha"] == _git_blob(
        calibration["candidateReachabilityTestPath"]
    )
    assert calibration["evaluatorContractTestGitBlobSha"] == _git_blob(
        calibration["evaluatorContractTestPath"]
    )
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
    assert runtime["exerciseUnits"]["c3_rejection_pages"] == "unique-page-witness"
    assert runtime["positiveGateIndependence"]["dedicatedMechanismPagesRequired"] is True
    invalid = runtime["invalidDiagnosticSemantics"]
    assert invalid["evaluatorBehavior"] == "raise-InvalidDiagnosticError-before-v2-delegation"
    assert invalid["futureRunnerHarnessClassification"] == "INVALID_EXPERIMENT"
    assert invalid["countsAsExercise"] is False


@pytest.mark.parametrize("metric", tuple(EXERCISE_MINIMA_V3))
def test_v3_evaluator_counts_true_state_and_rejects_nearby_mismatch(metric: str) -> None:
    slice_name, diagnostics = _case(metric)
    page = _page(slice_name)
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts[metric].count == 1

    _break_case(metric, diagnostics)
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts[metric].count == 0


def test_v3_c3_rejection_is_page_level_and_cannot_multiply_pair_evidence() -> None:
    region_ids = ("r1", "r2", "r3")
    diagnostics = _diagnostics(region_ids=region_ids)
    diagnostic = diagnostics[ArmId.C3_ONLY]["Q901"]
    diagnostic.update(
        {
            "preSegmentation": {
                "reliable": False,
                "reason": "fewer-than-two-groups",
                "boxCount": 1,
                "boxes": [_box(100, 100, 900, 900)],
            },
            "segmentation": {
                "reliable": False,
                "reason": "fewer-than-two-groups",
                "boxes": [_box(100, 100, 900, 900)],
            },
            "recoveryReason": "rejected-ambiguous-overlap",
            "usedPanelEvidence": False,
        }
    )
    page = PageGroundTruth(
        page_id="Q901",
        reading_order=region_ids,
        unscored_region_ids=(),
        qualification_pairs=(
            QualificationPair(
                "p1",
                "r1",
                "r2",
                ("c3-zero-multiple-anchor-negative",),
            ),
            QualificationPair(
                "p2",
                "r2",
                "r3",
                ("c3-invalid-topology-negative",),
            ),
        ),
        layout_tags=(),
    )
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    count = report.counts["c3_rejection_pages"]
    assert count.count == 1
    assert count.page_ids == ("Q901",)
    assert count.pair_ids == ()


def _expect_invalid(
    page: PageGroundTruth,
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> None:
    with pytest.raises(InvalidDiagnosticError):
        build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda diagnostics: diagnostics[ArmId.C2_ONLY]["Q901"].pop("relationEdges"),
        lambda diagnostics: diagnostics[ArmId.C2_ONLY]["Q901"].__setitem__(
            "relationEdges", {}
        ),
        lambda diagnostics: diagnostics[ArmId.C2_ONLY]["Q901"].pop("finalOrder"),
        lambda diagnostics: diagnostics[ArmId.C2_ONLY]["Q901"].__setitem__(
            "fallbackOrder", None
        ),
        lambda diagnostics: diagnostics[ArmId.C2_ONLY]["Q901"].__setitem__(
            "usedPanelEvidence", "false"
        ),
    ],
)
def test_v3_invalid_diagnostic_fields_are_harness_invalid(
    mutate: Callable[[dict[ArmId, dict[str, dict[str, object]]]], object],
) -> None:
    page = _page("c2-one-sided-non-unique-fail-closed")
    diagnostics = _diagnostics()
    diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = [_unassigned("r1", 0, "u000")]
    mutate(diagnostics)
    _expect_invalid(page, diagnostics)


def test_v3_missing_required_arm_is_harness_invalid() -> None:
    page = _page("c1-boundary-positive")
    diagnostics = _diagnostics()
    diagnostics.pop(ArmId.C1_ONLY)
    _expect_invalid(page, diagnostics)


def test_v3_missing_required_page_is_harness_invalid() -> None:
    page = _page("c1-boundary-positive")
    diagnostics = _diagnostics()
    diagnostics[ArmId.C1_ONLY].pop("Q901")
    _expect_invalid(page, diagnostics)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("experimentArm", ArmId.CONTROL.value),
        ("pageId", "Q902"),
        ("executionSha", "not-a-sha"),
    ],
)
def test_v3_inconsistent_identity_is_harness_invalid(field: str, value: object) -> None:
    page = _page("c1-boundary-positive")
    diagnostics = _diagnostics()
    diagnostics[ArmId.C1_ONLY]["Q901"][field] = value
    _expect_invalid(page, diagnostics)


def test_v3_mixed_execution_shas_are_harness_invalid() -> None:
    page = _page("c1-boundary-positive")
    diagnostics = _diagnostics()
    diagnostics[ArmId.C1_ONLY]["Q901"]["executionSha"] = "1" * 40
    _expect_invalid(page, diagnostics)


def test_v3_malformed_assignment_and_region_coverage_are_harness_invalid() -> None:
    page = _page("c1-boundary-positive")
    diagnostics = _diagnostics()
    diagnostics[ArmId.C1_ONLY]["Q901"]["assignments"] = [
        {
            "regionId": "r1",
            "sourceIndex": 0,
            "candidateGroupIndices": "not-a-list",
            "status": "unassigned",
            "reason": "boundary-guard",
            "assignedGroupIndex": None,
            "uncertainNodeLabel": "u000",
        }
    ]
    _expect_invalid(page, diagnostics)

    diagnostics = _diagnostics()
    diagnostics[ArmId.C1_ONLY]["Q901"]["regionDirections"] = {"r1": "v"}
    _expect_invalid(page, diagnostics)
