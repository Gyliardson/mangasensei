from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from scripts.research_lab.contracts import (
    ContractError,
    canonical_json_bytes,
    parse_command_comment,
)
from scripts.research_lab.github_api import (
    GitHubApiError,
    find_command_markers,
    list_issue_comments,
)


def _safe_protocol(
    *, status: str, reason_code: str, command_id: str = "", experiment_id: str = ""
) -> dict[str, str]:
    return {
        "schema_version": "mangasensei-research-protocol-status-v1",
        "protocol_status": status,
        "reason_code": reason_code,
        "command_id": command_id,
        "experiment_id": experiment_id,
    }


def validate_event_payload(
    event: Any,
    *,
    expected_repository: str,
    expected_issue: int,
    allowed_actor: str,
    expected_baseline: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not isinstance(event, dict):
        raise ContractError("event payload must be an object")
    if event.get("action") != "created":
        raise ContractError("event action must be created")
    repository = event.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != expected_repository:
        raise ContractError("event repository does not match control repository")
    issue = event.get("issue")
    if not isinstance(issue, dict) or issue.get("number") != expected_issue:
        raise ContractError("event issue does not match control issue")
    if "pull_request" in issue:
        raise ContractError("control command must originate from an issue, not a pull request")
    comment = event.get("comment")
    if not isinstance(comment, dict):
        raise ContractError("event comment is missing")
    user = comment.get("user")
    if not isinstance(user, dict) or user.get("login") != allowed_actor:
        raise ContractError("comment actor is not allowlisted")
    body = comment.get("body")
    command = parse_command_comment(body)
    if command.baseline_sha != expected_baseline:
        raise ContractError("command baseline is stale")
    payload = command.to_dict()
    payload["event_comment_id"] = str(comment.get("id", ""))
    protocol = _safe_protocol(
        status="accepted",
        reason_code="validated",
        command_id=command.command_id,
        experiment_id=command.experiment_id,
    )
    return payload, protocol


def _write_github_output(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ContractError("GitHub output values must be single-line")
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Research Lab issue_comment event")
    parser.add_argument("--event", type=Path, required=True)
    parser.add_argument("--expected-repository", required=True)
    parser.add_argument("--expected-issue", type=int, required=True)
    parser.add_argument("--allowed-actor", required=True)
    parser.add_argument("--expected-baseline", required=True)
    parser.add_argument("--command-out", type=Path, required=True)
    parser.add_argument("--protocol-out", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    try:
        event = json.loads(args.event.read_text(encoding="utf-8"))
        command_payload, protocol = validate_event_payload(
            event,
            expected_repository=args.expected_repository,
            expected_issue=args.expected_issue,
            allowed_actor=args.allowed_actor,
            expected_baseline=args.expected_baseline,
        )
    except (OSError, json.JSONDecodeError, ContractError):
        protocol = _safe_protocol(
            status="rejected", reason_code="event_or_command_validation_failed"
        )
        args.protocol_out.write_bytes(canonical_json_bytes(protocol))
        _write_github_output(
            args.github_output,
            {"protocol_status": "rejected", "command_id": "", "experiment_id": ""},
        )
        return 0

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        protocol = _safe_protocol(
            status="rejected",
            reason_code="github_token_unavailable",
            command_id=command_payload["command_id"],
            experiment_id=command_payload["experiment_id"],
        )
        args.protocol_out.write_bytes(canonical_json_bytes(protocol))
        _write_github_output(
            args.github_output,
            {
                "protocol_status": "rejected",
                "command_id": command_payload["command_id"],
                "experiment_id": command_payload["experiment_id"],
            },
        )
        return 0

    try:
        comments = list_issue_comments(
            token=token,
            repository=args.expected_repository,
            issue_number=args.expected_issue,
        )
    except GitHubApiError:
        protocol = _safe_protocol(
            status="rejected",
            reason_code="idempotency_check_failed_closed",
            command_id=command_payload["command_id"],
            experiment_id=command_payload["experiment_id"],
        )
        args.protocol_out.write_bytes(canonical_json_bytes(protocol))
        _write_github_output(
            args.github_output,
            {
                "protocol_status": "rejected",
                "command_id": command_payload["command_id"],
                "experiment_id": command_payload["experiment_id"],
            },
        )
        return 0

    matches = find_command_markers(comments, command_payload["command_id"])
    if matches:
        protocol = _safe_protocol(
            status="duplicate",
            reason_code="command_id_already_seen",
            command_id=command_payload["command_id"],
            experiment_id=command_payload["experiment_id"],
        )
        args.protocol_out.write_bytes(canonical_json_bytes(protocol))
        _write_github_output(
            args.github_output,
            {
                "protocol_status": "duplicate",
                "command_id": command_payload["command_id"],
                "experiment_id": command_payload["experiment_id"],
            },
        )
        return 0

    args.command_out.write_bytes(canonical_json_bytes(command_payload))
    args.protocol_out.write_bytes(canonical_json_bytes(protocol))
    _write_github_output(
        args.github_output,
        {
            "protocol_status": "accepted",
            "command_id": command_payload["command_id"],
            "experiment_id": command_payload["experiment_id"],
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
