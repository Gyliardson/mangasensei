from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.reading_order_post_v2_qualification.contracts import (
    ArmId,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_post_v2_qualification.exercise_v3 import (
    EXERCISE_MINIMA_V3,
    build_exercise_report_v3,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
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
    return subprocess.run(
        ["git", "rev-parse", f"HEAD:{path}"],
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


def _default_diag() -> dict[str, object]:
    return {
        "preSegmentation": {"reliable": True, "reason": "reliable", "boxCount": 2},
        "segmentation": {"reliable": True, "reason": "reliable"},
        "recoveryReason": "disabled",
        "assignments": [],
        "relationEdges": [],
        "fallbackReason": None,
        "usedPanelEvidence": True,
        "fallbackOrder": ["r1", "r2"],
        "finalOrder": ["r1", "r2"],
        "regionDirections": {},
    }


def _diagnostics() -> dict[ArmId, dict[str, dict[str, object]]]:
    return {arm: {"Q901": _default_diag()} for arm in ArmId}


def _case(metric: str) -> tuple[str, dict[ArmId, dict[str, dict[str, object]]]]:
    diagnostics = _diagnostics()
    if metric == "c1_guarded_pairs":
        slice_name = "c1-boundary-positive"
        diagnostics[ArmId.CONTROL]["Q901"]["assignments"] = [
            {
                "regionId": "r1",
                "status": "confident",
                "candidateGroupIndices": [0],
                "assignedGroupIndex": 0,
                "uncertainNodeLabel": None,
            }
        ]
        diagnostics[ArmId.C1_ONLY]["Q901"]["assignments"] = [
            {
                "regionId": "r1",
                "status": "unassigned",
                "candidateGroupIndices": [],
                "assignedGroupIndex": None,
                "uncertainNodeLabel": "u000",
            }
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
            {
                "regionId": "r1",
                "status": "unassigned",
                "candidateGroupIndices": [],
                "assignedGroupIndex": None,
                "uncertainNodeLabel": "u000",
            }
        ]
        diagnostics[ArmId.C2_ONLY]["Q901"]["relationEdges"] = [
            {"sourceNode": "g000", "targetNode": "u000", "rule": rule}
        ]
    elif metric == "c2_fail_closed_no_relation_pairs":
        slice_name = "c2-one-sided-non-unique-fail-closed"
        diagnostics[ArmId.C2_ONLY]["Q901"]["assignments"] = [
            {
                "regionId": "r1",
                "status": "unassigned",
                "candidateGroupIndices": [],
                "assignedGroupIndex": None,
                "uncertainNodeLabel": "u000",
            }
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
                },
                "segmentation": {"reliable": True, "reason": "recovered-merged-frame"},
                "recoveryReason": "accepted-strong-anchor-plus-occlusion-supported-frame",
                "usedPanelEvidence": True,
            }
        )
    elif metric == "c3_rejection_pairs":
        slice_name = "c3-invalid-topology-negative"
        diagnostics[ArmId.C3_ONLY]["Q901"].update(
            {
                "preSegmentation": {
                    "reliable": False,
                    "reason": "fewer-than-two-groups",
                    "boxCount": 1,
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
                    {"regionId": "r1", "status": "confident", "assignedGroupIndex": 0},
                    {"regionId": "r2", "status": "confident", "assignedGroupIndex": 0},
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
        diagnostics[ArmId.C3_ONLY]["Q901"]["recoveryReason"] = "rejected-no-unique-strong-frame-anchor"
    elif metric == "c3_rejection_pairs":
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
    assert methodology["runtimeReachability"]["exerciseMinima"] == EXERCISE_MINIMA_V3
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
    assert runtime["c3NegativeSemantics"] == "generic-rejection-only"
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


def test_v3_c3_negative_claim_is_generic_not_category_specific() -> None:
    _, diagnostics = _case("c3_rejection_pairs")
    page = _page("c3-zero-multiple-anchor-negative")
    report = build_exercise_report_v3(annotations=(page,), diagnostics=diagnostics)
    assert report.counts["c3_rejection_pairs"].count == 1
    assert not any(
        name.startswith("c3_zero_multiple_") or name.startswith("c3_invalid_topology_")
        or name.startswith("c3_insufficient_visible_")
        for name in report.counts
    )
