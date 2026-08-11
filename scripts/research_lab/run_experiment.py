from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from scripts.research_lab.contracts import ContractError, parse_command_payload
from scripts.research_lab.runner import execute_command, write_evidence


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one allowlisted Research Lab experiment")
    parser.add_argument("--command", type=Path, required=True)
    parser.add_argument("--expected-baseline", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    try:
        raw = json.loads(args.command.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ContractError("validated command file must be an object")
        expected_keys = {
            "command_id",
            "experiment_id",
            "baseline_sha",
            "spec_version",
            "parameters",
            "event_comment_id",
        }
        if set(raw) != expected_keys:
            raise ContractError("validated command file keys mismatch")
        command = parse_command_payload(
            {key: value for key, value in raw.items() if key != "event_comment_id"}
        )
        runtime_context = {
            "repository": os.environ.get("GITHUB_REPOSITORY", ""),
            "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
            "run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "runner_os": os.environ.get("RUNNER_OS", ""),
            "runner_arch": os.environ.get("RUNNER_ARCH", ""),
            "event_comment_id": str(raw["event_comment_id"]),
        }
        result, provenance = execute_command(
            command,
            expected_baseline=args.expected_baseline,
            runtime_context=runtime_context,
        )
        write_evidence(args.output_dir, result, provenance)
    except (OSError, ContractError, TypeError, ValueError) as exc:
        raise SystemExit(f"Research Lab experiment failed closed: {type(exc).__name__}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
