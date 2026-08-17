from __future__ import annotations

import copy
import hashlib
import json
import re
from fractions import Fraction
from pathlib import Path

import pytest
from scripts.reading_order_post_v2_qualification import (
    DIAGNOSTIC_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    EXPERIMENT_ID,
    SPEC_SCHEMA_VERSION,
)
from scripts.reading_order_post_v2_qualification.canonical import (
    sha256_path,
    verify_checksums,
    write_checksums,
    write_deterministic_zip,
)
from scripts.reading_order_post_v2_qualification.contracts import (
    SLICE_MINIMA,
    ArmId,
    ContractError,
    PageGroundTruth,
    QualificationPair,
    canonical_qualification_identity,
    load_corpus_design,
    validate_qualification_identity,
)
from scripts.reading_order_post_v2_qualification.exercise import build_exercise_report
from scripts.reading_order_post_v2_qualification.replay_guard import (
    EXECUTION_STEP_NAME,
    RUN_TITLE_PREFIX,
    assert_not_replayed,
    detect_duplicate,
    execution_step_observed,
)
from scripts.reading_order_post_v2_qualification.scoring import (
    candidate_only_wrong_pairs,
    score_corpus,
    score_page,
    strict_wrong_set_improvement,
    wrong_set_is_subset,
)
from scripts.reading_order_post_v2_qualification.spec import (
    LEGACY_SPEC_PATH,
    RETIRED_BINDING_V2,
    SpecError,
    validate_spec,
)
from scripts.reading_order_post_v2_qualification.verdict import (
    ComponentStatus,
    Verdict,
    evaluate_verdict,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = (
    REPO_ROOT
    / "scripts"
    / "reading_order_post_v2_qualification"
    / "spec"
    / "experiment-spec-v2.json"
)
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "reading-order-post-v2-qualification.yml"
CANDIDATE_PATH = (
    REPO_ROOT
    / "backend"
    / "src"
    / "mangasensei"
    / "ocr"
    / "diagnostics"
    / "reading_order_post_v2_calibration.py"
)
PRODUCTION_PATH = REPO_ROOT / "backend" / "src" / "mangasensei" / "ocr" / "reading_order.py"


def _resolved_spec() -> dict[str, object]:
    return validate_spec(SPEC_PATH)


def _pair(page_id: str, slice_name: str) -> PageGroundTruth:
    return PageGroundTruth(
        page_id=page_id,
        reading_order=("r1", "r2"),
        unscored_region_ids=(),
        qualification_pairs=(
            QualificationPair("p1", "r1", "r2", (slice_name,)),
        ),
        layout_tags=(),
    )


def _empty_diagnostics() -> dict[ArmId, dict[str, dict[str, object]]]:
    return {arm: {} for arm in ArmId}


def test_spec_declares_frozen_identity_constants_and_no_authorization() -> None:
    spec = _resolved_spec()
    assert spec["schemaVersion"] == SPEC_SCHEMA_VERSION
    assert spec["experimentId"] == EXPERIMENT_ID
    assert spec["diagnosticSchemaVersion"] == DIAGNOSTIC_SCHEMA_VERSION
    assert spec["evidenceBundleSchemaVersion"] == EVIDENCE_SCHEMA_VERSION
    assert spec["status"] == "FROZEN_NOT_AUTHORIZED_FOR_EXECUTION"
    assert spec["candidateBinding"] == {
        "commitSha": "f45facb2284d740df2f294800f705414e0ba465e",
        "sourceBlobSha": "ed1be14f4ad47c317ad755b94f1b3e23e84064da",
        "sourcePath": (
            "backend/src/mangasensei/ocr/diagnostics/"
            "reading_order_post_v2_calibration.py"
        ),
        "treeSha": "68418482b8ccf5d7a3cb1c9ef3834505bd20cd4c",
    }
    constants = {item["symbol"]: item for item in spec["numericConstants"]}  # type: ignore[index]
    assert constants["_BOUNDARY_GUARD_PX"]["value"] == 1
    assert constants["_BOUNDARY_GUARD_PX"]["provenanceCategory"] == "A"
    assert constants["_FRAME_MIN_SIDE_COVERAGE"]["value"] == 0.8
    assert constants["_FRAME_MIN_SIDE_COVERAGE"]["provenanceCategory"] == "D"
    assert constants["minor_major_axis_ratio"]["provenanceCategory"] == "D"
    assert constants["_panel_precedence_edges.same_level.x_overlap_max"]["value"] == 0.15
    assert constants["_panel_precedence_edges.aligned.y_overlap_max"]["value"] == 0.2
    freeze = spec["freezeBoundaries"]
    assert isinstance(freeze, dict)
    assert freeze["noFutureHeldoutAuthoredOrRevealedByFreeze"] is True
    assert freeze["noQualificationExecutionAuthorizedBySpec"] is True
    assert freeze["noProductionActivation"] is True
    assert freeze["noRetiredPostV2HeldoutV1Reuse"] is True
    assert spec["retiredPostV2HeldoutV1Binding"] == RETIRED_BINDING_V2
    assert spec["baselineProductionBinding"] == {
        "commitSha": "f45facb2284d740df2f294800f705414e0ba465e",
        "role": (
            "current-post-v2-production-reading-order-dependency-not-modified-or-"
            "activated-by-this-experiment"
        ),
        "sourceBlobSha": "12358a59deee7bd0ec0845963da1b98f031592f1",
        "sourcePath": "backend/src/mangasensei/ocr/reading_order.py",
        "treeSha": "68418482b8ccf5d7a3cb1c9ef3834505bd20cd4c",
    }
    assert spec["historicalV2ProductionBaselineBinding"] == {
        "commitSha": "292f0a8c8142d919ac4184159d102789c43b4116",
        "treeSha": "6605f6de429b318139fb91a4535ebbd2193508ce",
        "sourcePath": "backend/src/mangasensei/ocr/reading_order.py",
        "sourceBlobSha": "122f575c1c3567787aec29da0b1996fe0bf3e110",
        "role": "historical-reading-order-v2-production-baseline-identity",
        "contentEquivalentToCurrentPostV2Baseline": False,
        "relationship": (
            "Historical Reading Order v2 production baseline identity only; the post-v2 "
            "candidate is evaluated against the current production dependency recorded in "
            "baselineProductionBinding. The source blobs are intentionally distinct."
        ),
    }


def test_v2_overlay_preserves_v1_scientific_methodology_exactly() -> None:
    legacy = json.loads(LEGACY_SPEC_PATH.read_text(encoding="utf-8"))
    resolved = copy.deepcopy(_resolved_spec())

    resolved["schemaVersion"] = legacy["schemaVersion"]
    resolved["experimentId"] = legacy["experimentId"]
    freeze = resolved["freezeBoundaries"]
    assert isinstance(freeze, dict)
    freeze.pop("noRetiredPostV2HeldoutV1Reuse")
    design_coverage = resolved["newHeldoutDesignCoverage"]
    assert isinstance(design_coverage, dict)
    design_coverage["authoringBoundary"] = legacy["newHeldoutDesignCoverage"]["authoringBoundary"]
    resolved.pop("retiredPostV2HeldoutV1Binding")
    resolved["sourceBindings"] = legacy["sourceBindings"]
    workflow_contract = resolved["workflowContract"]
    assert isinstance(workflow_contract, dict)
    workflow_contract["gitBlobSha"] = legacy["workflowContract"]["gitBlobSha"]
    workflow_contract["preEnvironmentChecks"] = legacy["workflowContract"]["preEnvironmentChecks"]

    assert resolved == legacy


def test_historical_v1_spec_is_retired_and_non_executable() -> None:
    with pytest.raises(SpecError, match="retired and non-executable"):
        validate_spec(LEGACY_SPEC_PATH)


def test_spec_fixes_eight_attributable_arms_and_required_slices() -> None:
    spec = _resolved_spec()
    assert spec["arms"] == [arm.value for arm in ArmId]
    arm_rationale = spec["armRationale"]
    assert isinstance(arm_rationale, dict)
    assert arm_rationale["keepAllEight"] is True
    slice_minima = spec["sliceMinima"]
    assert isinstance(slice_minima, dict)
    assert slice_minima["c3-positive-recovery"] == {"minPairs": 10, "minPages": 4}
    assert slice_minima["clean-control"] == {"minPairs": 16, "minPages": 6}
    requirements = spec["corpusDesignRequirements"]
    assert isinstance(requirements, dict)
    assert requirements["minimumPageCount"] == 24
    assert requirements["minimumQualificationPairs"] == 120


def test_qualification_identity_binds_all_frozen_inputs() -> None:
    parts = {
        "experiment_id": EXPERIMENT_ID,
        "spec_sha256": "1" * 64,
        "manifest_sha256": "2" * 64,
        "design_sha256": "3" * 64,
        "execution_sha": "4" * 40,
        "execution_tree_sha": "5" * 40,
    }
    identity = canonical_qualification_identity(**parts)
    assert re.fullmatch(r"ropv2q-[0-9a-f]{64}", identity)
    validate_qualification_identity(identity, **parts)
    changed = dict(parts)
    changed["manifest_sha256"] = "6" * 64
    with pytest.raises(ContractError, match="does not match"):
        validate_qualification_identity(identity, **changed)


def test_scoring_uses_gt_precedence_pairs_not_geometry() -> None:
    gt = PageGroundTruth(
        page_id="Q001",
        reading_order=("a", "b", "c"),
        unscored_region_ids=(),
        qualification_pairs=(
            QualificationPair("p1", "a", "b", ("clean-control",)),
            QualificationPair("p2", "b", "c", ("clean-control",)),
        ),
        layout_tags=(),
    )
    exact = score_page(gt, ("a", "b", "c"))
    inverted = score_page(gt, ("b", "a", "c"))
    assert exact.aggregate.comparable_pairs == 3
    assert exact.aggregate.wrong_pairs_count == 0
    assert inverted.aggregate.wrong_pairs == (("Q001", "a", "b"),)
    assert inverted.aggregate.pairwise_accuracy == Fraction(2, 3)
    assert inverted.aggregate.normalized_error == Fraction(1, 3)
    assert inverted.exact_sequence is False
    corpus_exact = score_corpus((exact,))
    corpus_inverted = score_corpus((inverted,))
    assert wrong_set_is_subset(corpus_inverted.aggregate, corpus_exact.aggregate)
    assert strict_wrong_set_improvement(corpus_inverted.aggregate, corpus_exact.aggregate)
    assert candidate_only_wrong_pairs(corpus_exact.aggregate, corpus_inverted.aggregate) == (
        ("Q001", "a", "b"),
    )


def test_c3_negative_exercise_requires_exact_fail_closed_state() -> None:
    page = _pair("Q001", "c3-invalid-topology-negative")
    diagnostics = _empty_diagnostics()
    rejected = {
        "preSegmentation": {
            "reliable": False,
            "reason": "fewer-than-two-groups",
            "boxCount": 1,
        },
        "recoveryReason": "rejected-nested-panel-candidates",
        "assignments": [],
        "relationEdges": [],
        "finalOrder": ["r1", "r2"],
        "fallbackOrder": ["r1", "r2"],
        "usedPanelEvidence": False,
    }
    for arm in ArmId:
        diagnostics[arm]["Q001"] = dict(rejected)
    report = build_exercise_report(annotations=(page,), diagnostics=diagnostics)
    assert report.counts["c3_invalid_topology_rejection_pairs"].count == 1

    diagnostics[ArmId.C3_ONLY]["Q001"] = {**rejected, "assignments": [{"regionId": "r1"}]}
    diagnostics[ArmId.C1_C2_C3]["Q001"] = {**rejected, "relationEdges": [{"rule": "x"}]}
    diagnostics[ArmId.C1_C2_C3_B1]["Q001"] = {**rejected, "usedPanelEvidence": True}
    report = build_exercise_report(annotations=(page,), diagnostics=diagnostics)
    assert report.counts["c3_invalid_topology_rejection_pairs"].count == 0


def test_c2_gutter_exercise_is_bound_to_qualification_pair() -> None:
    page = _pair("Q001", "c2-gutter-bridge")
    diagnostics = _empty_diagnostics()
    default = {
        "assignments": [],
        "relationEdges": [],
        "fallbackReason": None,
        "usedPanelEvidence": True,
        "finalOrder": ["r1", "r2"],
        "fallbackOrder": ["r1", "r2"],
        "regionDirections": {},
    }
    for arm in ArmId:
        diagnostics[arm]["Q001"] = dict(default)
    diagnostics[ArmId.C2_ONLY]["Q001"] = {
        **default,
        "assignments": [
            {
                "regionId": "r1",
                "status": "unassigned",
                "uncertainNodeLabel": "u000",
            }
        ],
        "relationEdges": [
            {
                "sourceNode": "g000",
                "targetNode": "u000",
                "rule": "unique-gutter-between-hard-panels",
            }
        ],
    }
    report = build_exercise_report(annotations=(page,), diagnostics=diagnostics)
    assert report.counts["c2_gutter_pairs"].pair_ids == ("Q001:p1",)


def test_invalid_experiment_verdict_precedes_component_logic() -> None:
    result = evaluate_verdict(
        harness_valid=False,
        scores={},
        exercise=build_exercise_report(annotations=(), diagnostics=_empty_diagnostics()),
    )
    assert result.verdict is Verdict.INVALID_EXPERIMENT
    assert result.c1_status is ComponentStatus.NOT_EVALUATED
    assert result.final_status is ComponentStatus.NOT_EVALUATED


def test_replay_guard_rejects_only_observed_execution_step() -> None:
    identity = "ropv2q-" + "a" * 64
    title = f"{RUN_TITLE_PREFIX}{identity}"
    runs = [
        {"id": 10, "display_title": title},
        {"id": 11, "display_title": title},
        {"id": 12, "display_title": "other"},
    ]
    jobs = {
        10: {
            "jobs": [
                {
                    "steps": [
                        {
                            "name": EXECUTION_STEP_NAME,
                            "status": "completed",
                            "conclusion": "skipped",
                        }
                    ]
                }
            ]
        },
        11: {
            "jobs": [
                {
                    "steps": [
                        {
                            "name": EXECUTION_STEP_NAME,
                            "status": "completed",
                            "conclusion": "failure",
                        }
                    ]
                }
            ]
        },
    }
    duplicate = detect_duplicate(
        runs=runs,
        qualification_identity=identity,
        current_run_id=99,
        jobs_loader=lambda run_id: jobs[run_id],
    )
    assert duplicate == 11
    assert execution_step_observed(jobs[10]) is False
    assert execution_step_observed(jobs[11]) is True


def test_replay_guard_url_trust_boundary_is_fail_closed() -> None:
    common = {
        "workflow_file": "reading-order-post-v2-qualification.yml",
        "qualification_identity": "ropv2q-" + "a" * 64,
        "current_run_id": 1,
        "token": "synthetic-test-token",
    }
    for api_url in (
        "http://api.github.com",
        "https://evil.example",
        "https://user@api.github.com",
        "https://api.github.com:8443",
    ):
        with pytest.raises(RuntimeError):
            assert_not_replayed(
                repository="Gyliardson/mangasensei",
                api_url=api_url,
                **common,
            )
    with pytest.raises(RuntimeError, match="repository"):
        assert_not_replayed(
            repository="other/repository",
            api_url="https://api.github.com",
            **common,
        )


def test_corpus_design_rejects_historical_identity_before_any_assets(tmp_path: Path) -> None:
    design = {
        "schemaVersion": "reading-order-post-v2-corpus-design-v1",
        "corpusId": "mangasensei-reading-order-heldout-v2",
        "version": "1.0.0",
        "pageIds": [f"Q{index:03d}" for index in range(1, 25)],
        "requirements": {
            "minimumPageCount": 24,
            "minimumQualificationPairs": 120,
            "minimumScoredRegions": 96,
            "minimumCombinedMechanismPages": 4,
            "minimumIntentionalFallbackPages": 3,
            "minimumCleanControlPages": 6,
        },
        "requiredSlices": SLICE_MINIMA,
        "authorshipBoundary": "new-project-authored-no-historical-v2-case-reuse",
    }
    path = tmp_path / "corpus-design.json"
    path.write_text(json.dumps(design), encoding="utf-8")
    with pytest.raises(ContractError, match="new non-historical corpusId"):
        load_corpus_design(path)


def test_deterministic_zip_bytes_and_checksums(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        (root / "b.txt").write_text("beta\n", encoding="utf-8")
        (root / "a.txt").write_text("alpha\n", encoding="utf-8")
        write_checksums(root)
        verify_checksums(root)
    first_zip = tmp_path / "first.zip"
    second_zip = tmp_path / "second.zip"
    write_deterministic_zip(first, first_zip)
    write_deterministic_zip(second, second_zip)
    assert first_zip.read_bytes() == second_zip.read_bytes()
    assert sha256_path(first_zip) == sha256_path(second_zip)
    assert hashlib.sha256(first_zip.read_bytes()).hexdigest() == sha256_path(first_zip)


def test_workflow_is_manual_least_privilege_pinned_and_fail_closed() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    assert "\n  pull_request:" not in workflow
    assert "permissions:\n  contents: read\n  actions: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "authorize_new_qualification" in workflow
    assert "test \"$AUTHORIZED\" = \"true\"" in workflow
    assert f'test "$EXPERIMENT_ID" = "{EXPERIMENT_ID}"' in workflow
    assert "EXPERIMENT_SPEC: scripts/reading_order_post_v2_qualification/spec/experiment-spec-v2.json" in workflow
    assert "refs/remotes/origin/main" in workflow
    assert "test -z \"$(git status --porcelain)\"" in workflow
    assert workflow.index("Reject duplicate or replayed observed identity") < workflow.index(
        "Install uv"
    )
    assert workflow.count("name: Execute frozen qualification exactly once") == 1
    assert "scripts.reading_order_v2.run_heldout" not in workflow
    assert "retention-days: 90" in workflow
    assert "if: always()" in workflow
    uses = re.findall(r"uses: ([^\s]+)", workflow)
    assert uses
    for action in uses:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), action


def test_candidate_and_production_sources_are_not_modified_by_freeze_spec() -> None:
    assert CANDIDATE_PATH.is_file()
    assert PRODUCTION_PATH.is_file()
    candidate = CANDIDATE_PATH.read_text(encoding="utf-8")
    production = PRODUCTION_PATH.read_text(encoding="utf-8")
    assert "_BOUNDARY_GUARD_PX = 1" in candidate
    assert "def run_post_v2_calibration_candidate" in candidate
    assert "def manga_tier_order" in production
