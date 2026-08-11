from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.research_lab.contracts import (
    COMMAND_SENTINEL,
    EXPERIMENT_SPEC_VERSION,
    ContractError,
    ResearchCommand,
)
from scripts.research_lab.github_api import find_command_markers
from scripts.research_lab.post_comment import build_failure_comment, build_result_comment
from scripts.research_lab.runner import execute_command, write_evidence
from scripts.research_lab.validate_event import validate_event_payload

BASELINE = "5" * 40


def _body(command_id: str = "smoke-command-001") -> str:
    return (
        f"{COMMAND_SENTINEL}\n"
        + json.dumps(
            {
                "command_id": command_id,
                "experiment_id": "framework-smoke-v1",
                "baseline_sha": BASELINE,
                "spec_version": EXPERIMENT_SPEC_VERSION,
                "parameters": {"repeat": 1},
            }
        )
    )


def _event() -> dict[str, object]:
    return {
        "action": "created",
        "repository": {"full_name": "Gyliardson/mangasensei"},
        "issue": {"number": 132},
        "comment": {"id": 987, "body": _body(), "user": {"login": "Gyliardson"}},
    }


def test_event_validation_accepts_only_control_boundary() -> None:
    command, protocol = validate_event_payload(
        _event(),
        expected_repository="Gyliardson/mangasensei",
        expected_issue=132,
        allowed_actor="Gyliardson",
        expected_baseline=BASELINE,
    )
    assert command["event_comment_id"] == "987"
    assert protocol["protocol_status"] == "accepted"


@pytest.mark.parametrize("mutation", ["actor", "repository", "issue", "pull_request", "stale"])
def test_event_validation_rejects_wrong_boundary(mutation: str) -> None:
    event = _event()
    expected_baseline = BASELINE
    if mutation == "actor":
        event["comment"] = {"id": 987, "body": _body(), "user": {"login": "mallory"}}
    elif mutation == "repository":
        event["repository"] = {"full_name": "mallory/fork"}
    elif mutation == "issue":
        event["issue"] = {"number": 999}
    elif mutation == "pull_request":
        event["issue"] = {"number": 132, "pull_request": {"url": "ignored"}}
    else:
        expected_baseline = "6" * 40
    with pytest.raises(ContractError):
        validate_event_payload(
            event,
            expected_repository="Gyliardson/mangasensei",
            expected_issue=132,
            allowed_actor="Gyliardson",
            expected_baseline=expected_baseline,
        )


def test_duplicate_command_markers_are_recognized_without_reexecution() -> None:
    comments = [
        {
            "id": 1,
            "body": (
                'MANGASENSEI_RESEARCH_STATUS_V1\n'
                '{"command_id":"smoke-command-001","state":"RUNNING"}'
            ),
        },
        {
            "id": 2,
            "body": (
                'MANGASENSEI_RESEARCH_RESULT_V1\n'
                '{"command_id":"smoke-command-001",'
                '"state":"NEEDS_ANALYSIS","run_id":"123"}'
            ),
        },
        {
            "id": 3,
            "body": (
                'MANGASENSEI_RESEARCH_RESULT_V1\n'
                '{"command_id":"other-command-001","state":"NEEDS_ANALYSIS"}'
            ),
        },
    ]
    matches = find_command_markers(comments, "smoke-command-001")
    assert [item["comment_id"] for item in matches] == [1, 2]


def test_result_comment_points_to_exact_evidence(tmp_path: Path) -> None:
    command = ResearchCommand(
        command_id="smoke-command-001",
        experiment_id="framework-smoke-v1",
        baseline_sha=BASELINE,
        spec_version=EXPERIMENT_SPEC_VERSION,
        parameters={"repeat": 1},
    )
    result, provenance = execute_command(command, expected_baseline=BASELINE, runtime_context={})
    output = tmp_path / "evidence"
    write_evidence(output, result, provenance)
    command_payload = command.to_dict() | {"event_comment_id": "987"}
    body = build_result_comment(
        command_payload,
        output,
        artifact_id="456",
        artifact_name="research-smoke-command-001",
        artifact_digest="a" * 64,
        artifact_url="https://github.com/Gyliardson/mangasensei/actions/runs/123/artifacts/456",
        repository="Gyliardson/mangasensei",
        run_id="123",
        run_attempt="1",
    )
    assert body.startswith("MANGASENSEI_RESEARCH_RESULT_V1\n")
    payload = json.loads(body.split("\n", 1)[1])
    assert payload["state"] == "NEEDS_ANALYSIS"
    assert payload["artifact_id"] == "456"
    assert payload["artifact_digest"] == "a" * 64
    assert payload["run_url"] == "https://github.com/Gyliardson/mangasensei/actions/runs/123"
    assert len(payload["results_sha256"]) == 64


def test_failure_comment_closes_running_state() -> None:
    command = ResearchCommand(
        command_id="smoke-command-001",
        experiment_id="framework-smoke-v1",
        baseline_sha=BASELINE,
        spec_version=EXPERIMENT_SPEC_VERSION,
        parameters={"repeat": 1},
    )
    body = build_failure_comment(
        command.to_dict() | {"event_comment_id": "987"},
        run_id="123",
        run_attempt="1",
    )
    payload = json.loads(body.split("\n", 1)[1])
    assert payload["state"] == "BLOCKED"
    assert payload["reason_code"] == "experiment_or_artifact_failed"


def test_invalid_secret_bearing_comment_is_not_echoed_by_event_error() -> None:
    event = _event()
    event["comment"] = {
        "id": 987,
        "body": f'{COMMAND_SENTINEL}\n{{"secret":"sk-not-real-sensitive-value"}}',
        "user": {"login": "Gyliardson"},
    }
    with pytest.raises(ContractError) as exc_info:
        validate_event_payload(
            event,
            expected_repository="Gyliardson/mangasensei",
            expected_issue=132,
            allowed_actor="Gyliardson",
            expected_baseline=BASELINE,
        )
    assert "sk-not-real-sensitive-value" not in str(exc_info.value)
