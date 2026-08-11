from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from scripts.research_lab.contracts import (
    RESULT_SENTINEL,
    STATUS_SENTINEL,
    canonical_json_bytes,
    sha256_file,
)
from scripts.research_lab.github_api import GitHubApiError, post_issue_comment


def _json_line(payload: dict[str, Any]) -> str:
    return canonical_json_bytes(payload).decode("utf-8").strip()


def build_protocol_comment(protocol: dict[str, Any], *, run_id: str, run_attempt: str) -> str:
    status = protocol.get("protocol_status")
    state = "BLOCKED" if status in {"rejected", "duplicate"} else "QUEUED"
    payload = {
        "schema_version": "mangasensei-research-status-v1",
        "command_id": protocol.get("command_id", ""),
        "experiment_id": protocol.get("experiment_id", ""),
        "state": state,
        "reason_code": protocol.get("reason_code", ""),
        "run_id": run_id,
        "run_attempt": run_attempt,
    }
    return f"{STATUS_SENTINEL}\n{_json_line(payload)}"


def build_running_comment(
    command: dict[str, Any], *, run_id: str, run_attempt: str
) -> str:
    payload = {
        "schema_version": "mangasensei-research-status-v1",
        "command_id": command["command_id"],
        "experiment_id": command["experiment_id"],
        "baseline_sha": command["baseline_sha"],
        "state": "RUNNING",
        "run_id": run_id,
        "run_attempt": run_attempt,
    }
    return f"{STATUS_SENTINEL}\n{_json_line(payload)}"


def build_failure_comment(
    command: dict[str, Any], *, run_id: str, run_attempt: str
) -> str:
    payload = {
        "schema_version": "mangasensei-research-status-v1",
        "command_id": command["command_id"],
        "experiment_id": command["experiment_id"],
        "baseline_sha": command["baseline_sha"],
        "state": "BLOCKED",
        "reason_code": "experiment_or_artifact_failed",
        "run_id": run_id,
        "run_attempt": run_attempt,
    }
    return f"{STATUS_SENTINEL}\n{_json_line(payload)}"


def build_result_comment(
    command: dict[str, Any],
    output_dir: Path,
    *,
    artifact_id: str,
    artifact_name: str,
    artifact_digest: str,
    artifact_url: str,
    repository: str,
    run_id: str,
    run_attempt: str,
) -> str:
    result = json.loads((output_dir / "results.json").read_text(encoding="utf-8"))
    payload = {
        "schema_version": "mangasensei-research-result-comment-v1",
        "command_id": command["command_id"],
        "experiment_id": command["experiment_id"],
        "baseline_sha": command["baseline_sha"],
        "state": "NEEDS_ANALYSIS",
        "decision": result["decision"],
        "run_id": run_id,
        "run_attempt": run_attempt,
        "run_url": f"https://github.com/{repository}/actions/runs/{run_id}",
        "artifact_id": artifact_id,
        "artifact_name": artifact_name,
        "artifact_digest": artifact_digest,
        "artifact_url": artifact_url,
        "results_sha256": sha256_file(output_dir / "results.json"),
        "provenance_sha256": sha256_file(output_dir / "provenance.json"),
        "checksums_sha256": sha256_file(output_dir / "checksums.sha256"),
    }
    return f"{RESULT_SENTINEL}\n{_json_line(payload)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Post a bounded Research Lab control comment")
    parser.add_argument(
        "--kind", choices=("protocol", "running", "failure", "result"), required=True
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--protocol", type=Path)
    parser.add_argument("--command", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--artifact-name", default="")
    parser.add_argument("--artifact-digest", default="")
    parser.add_argument("--artifact-url", default="")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required only for the bounded comment-posting step")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "")

    if args.kind == "protocol":
        if args.protocol is None:
            raise SystemExit("--protocol is required")
        payload = json.loads(args.protocol.read_text(encoding="utf-8"))
        body = build_protocol_comment(payload, run_id=run_id, run_attempt=run_attempt)
    elif args.kind in {"running", "failure"}:
        if args.command is None:
            raise SystemExit("--command is required")
        command = json.loads(args.command.read_text(encoding="utf-8"))
        if args.kind == "running":
            body = build_running_comment(command, run_id=run_id, run_attempt=run_attempt)
        else:
            body = build_failure_comment(command, run_id=run_id, run_attempt=run_attempt)
    else:
        if args.command is None or args.output_dir is None:
            raise SystemExit("--command and --output-dir are required")
        command = json.loads(args.command.read_text(encoding="utf-8"))
        body = build_result_comment(
            command,
            args.output_dir,
            artifact_id=args.artifact_id,
            artifact_name=args.artifact_name,
            artifact_digest=args.artifact_digest,
            artifact_url=args.artifact_url,
            repository=args.repository,
            run_id=run_id,
            run_attempt=run_attempt,
        )

    try:
        post_issue_comment(
            token=token,
            repository=args.repository,
            issue_number=args.issue,
            body=body,
        )
    except GitHubApiError as exc:
        raise SystemExit("failed to post bounded Research Lab control comment") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
