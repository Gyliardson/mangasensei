from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from scripts.research_lab.contracts import (
    EXPERIMENT_SPEC_VERSION,
    PROVENANCE_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    ContractError,
    ResearchCommand,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
    validate_catalog,
    validate_experiment_parameters,
)

_CATALOG_PATH = Path(__file__).with_name("catalog.json")
_FIXTURES: dict[str, Path] = {
    "framework-smoke-v1": Path(__file__).with_name("fixtures") / "framework-smoke-v1.txt",
}


def _load_catalog() -> tuple[dict[str, dict[str, Any]], str]:
    raw_bytes = _CATALOG_PATH.read_bytes()
    try:
        raw = json.loads(raw_bytes)
    except json.JSONDecodeError as exc:
        raise ContractError(f"catalog is invalid JSON: {exc.msg}") from exc
    validated = validate_catalog(raw)
    return {entry["experiment_id"]: entry for entry in validated}, sha256_bytes(raw_bytes)


def _framework_smoke(
    spec: dict[str, Any], repeat: int
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    fixture_id = spec["input"]["fixture_id"]
    fixture_path = _FIXTURES.get(fixture_id)
    if fixture_path is None:
        raise ContractError(f"fixture_id is not compiled into the allowlist: {fixture_id}")
    expected_sha = spec["input"]["sha256"]
    actual_sha = sha256_file(fixture_path)
    if actual_sha != expected_sha:
        raise ContractError(
            "frozen fixture checksum mismatch for "
            f"{fixture_id}: expected {expected_sha}, got {actual_sha}"
        )

    payload = fixture_path.read_bytes()
    negative_payload = payload + b"\x00mangasensei-research-negative-control-v1"
    negative_sha = sha256_bytes(negative_payload)
    if negative_sha == expected_sha:
        raise ContractError("negative control unexpectedly collides with fixture digest")

    cases: list[dict[str, Any]] = []
    positive_matches = 0
    negative_distinct = 0
    for index in range(repeat):
        observed = sha256_bytes(payload)
        negative_observed = sha256_bytes(negative_payload)
        positive_ok = observed == expected_sha
        negative_ok = negative_observed != expected_sha
        positive_matches += int(positive_ok)
        negative_distinct += int(negative_ok)
        cases.append(
            {
                "case_id": f"repeat-{index + 1:02d}",
                "input_sha256": expected_sha,
                "observed_sha256": observed,
                "negative_control_sha256": negative_observed,
                "positive_control_passed": positive_ok,
                "negative_control_passed": negative_ok,
            }
        )
    counts = {
        "requested_cases": repeat,
        "completed_cases": len(cases),
        "positive_matches": positive_matches,
        "negative_distinct": negative_distinct,
        "invalid_cases": 0,
    }
    return cases, counts


_IMPLEMENTATIONS: dict[
    str, Callable[[dict[str, Any], int], tuple[list[dict[str, Any]], dict[str, int]]]
] = {
    "framework-smoke-v1": _framework_smoke,
}


def execute_command(
    command: ResearchCommand,
    *,
    expected_baseline: str,
    runtime_context: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if command.baseline_sha != expected_baseline:
        raise ContractError(
            f"stale baseline_sha: command={command.baseline_sha}, current={expected_baseline}"
        )
    if command.spec_version != EXPERIMENT_SPEC_VERSION:
        raise ContractError("unsupported experiment spec version")

    catalog, catalog_sha = _load_catalog()
    spec = catalog.get(command.experiment_id)
    if spec is None:
        raise ContractError(f"experiment_id is not allowlisted: {command.experiment_id}")
    if spec["spec_version"] != command.spec_version:
        raise ContractError("command spec_version does not match allowlisted experiment")
    repeat = validate_experiment_parameters(spec, command.parameters)
    implementation_id = spec["implementation"]
    implementation = _IMPLEMENTATIONS.get(implementation_id)
    if implementation is None:
        raise ContractError(f"implementation is not compiled into the runner: {implementation_id}")

    start = time.monotonic_ns()
    cases, counts = implementation(spec, repeat)
    elapsed_ns = time.monotonic_ns() - start
    max_runtime_ns = spec["max_runtime_seconds"] * 1_000_000_000
    if elapsed_ns > max_runtime_ns:
        raise ContractError("experiment exceeded frozen max_runtime_seconds")
    success = (
        counts["positive_matches"] == repeat
        and counts["negative_distinct"] == repeat
        and counts["invalid_cases"] == 0
    )

    spec_digest = sha256_bytes(canonical_json_bytes(spec))
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "command_id": command.command_id,
        "experiment_id": command.experiment_id,
        "spec_version": command.spec_version,
        "baseline_sha": command.baseline_sha,
        "state": "RESULT_AVAILABLE",
        "decision": "PASS" if success else "FAIL",
        "parameters": command.parameters,
        "spec_sha256": spec_digest,
        "input": spec["input"],
        "counts": counts,
        "cases": cases,
        "success_gate": spec["success_gate"],
        "stop_condition": spec["stop_condition"],
    }

    runtime = dict(runtime_context or {})
    provenance = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "command_id": command.command_id,
        "experiment_id": command.experiment_id,
        "baseline_sha": command.baseline_sha,
        "catalog_sha256": catalog_sha,
        "spec_sha256": spec_digest,
        "result_sha256": sha256_bytes(canonical_json_bytes(result)),
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "elapsed_ns": elapsed_ns,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "repository": runtime.get("repository", ""),
        "workflow": runtime.get("workflow", ""),
        "run_id": runtime.get("run_id", ""),
        "run_attempt": runtime.get("run_attempt", ""),
        "runner_os": runtime.get("runner_os", os.environ.get("RUNNER_OS", "")),
        "runner_arch": runtime.get("runner_arch", os.environ.get("RUNNER_ARCH", "")),
        "event_comment_id": runtime.get("event_comment_id", ""),
    }
    return result, provenance


def write_evidence(
    output_dir: Path, result: dict[str, Any], provenance: dict[str, Any]
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=False)
    files = {
        "results.json": canonical_json_bytes(result),
        "provenance.json": canonical_json_bytes(provenance),
    }
    hashes: dict[str, str] = {}
    for name, data in files.items():
        path = output_dir / name
        path.write_bytes(data)
        hashes[name] = sha256_bytes(data)
    checksum_lines = [f"{hashes[name]}  {name}" for name in sorted(hashes)]
    checksum_bytes = ("\n".join(checksum_lines) + "\n").encode("utf-8")
    (output_dir / "checksums.sha256").write_bytes(checksum_bytes)
    hashes["checksums.sha256"] = sha256_bytes(checksum_bytes)
    return hashes
