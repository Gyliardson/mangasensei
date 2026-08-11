from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COMMAND_SENTINEL = "MANGASENSEI_RESEARCH_COMMAND_V1"
STATUS_SENTINEL = "MANGASENSEI_RESEARCH_STATUS_V1"
RESULT_SENTINEL = "MANGASENSEI_RESEARCH_RESULT_V1"
COMMAND_SCHEMA_VERSION = "mangasensei-research-command-v1"
EXPERIMENT_SPEC_VERSION = "mangasensei-research-experiment-spec-v1"
RESULT_SCHEMA_VERSION = "mangasensei-research-result-v1"
PROVENANCE_SCHEMA_VERSION = "mangasensei-research-provenance-v1"
CATALOG_SCHEMA_VERSION = "mangasensei-research-catalog-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{7,63}$")


class ContractError(ValueError):
    """Raised when a Research Lab contract fails closed."""


@dataclass(frozen=True)
class ResearchCommand:
    command_id: str
    experiment_id: str
    baseline_sha: str
    spec_version: str
    parameters: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "experiment_id": self.experiment_id,
            "baseline_sha": self.baseline_sha,
            "spec_version": self.spec_version,
            "parameters": self.parameters,
        }


def canonical_json_bytes(value: Any) -> bytes:
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return f"{serialized}\n".encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expect_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ContractError(f"{label} keys mismatch: missing={missing}, unknown={unknown}")


def _expect_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ContractError(f"{label} must be a string")
    return value


def parse_command_payload(payload: Any) -> ResearchCommand:
    if not isinstance(payload, dict):
        raise ContractError("command payload must be a JSON object")
    _expect_exact_keys(
        payload,
        {"command_id", "experiment_id", "baseline_sha", "spec_version", "parameters"},
        "command",
    )

    command_id = _expect_string(payload["command_id"], "command_id")
    experiment_id = _expect_string(payload["experiment_id"], "experiment_id")
    baseline_sha = _expect_string(payload["baseline_sha"], "baseline_sha")
    spec_version = _expect_string(payload["spec_version"], "spec_version")
    parameters = payload["parameters"]

    if not _ID_RE.fullmatch(command_id):
        raise ContractError("command_id must match the bounded lowercase id contract")
    if not _ID_RE.fullmatch(experiment_id):
        raise ContractError("experiment_id must match the bounded lowercase id contract")
    if not _GIT_SHA_RE.fullmatch(baseline_sha):
        raise ContractError("baseline_sha must be a full lowercase 40-character Git SHA")
    if spec_version != EXPERIMENT_SPEC_VERSION:
        raise ContractError(f"unsupported spec_version: {spec_version}")
    if not isinstance(parameters, dict):
        raise ContractError("parameters must be a JSON object")

    return ResearchCommand(
        command_id=command_id,
        experiment_id=experiment_id,
        baseline_sha=baseline_sha,
        spec_version=spec_version,
        parameters=parameters,
    )


def parse_command_comment(body: str) -> ResearchCommand:
    if not isinstance(body, str):
        raise ContractError("comment body must be a string")
    lines = body.splitlines()
    if not lines or lines[0] != COMMAND_SENTINEL:
        raise ContractError(f"comment must start with exact sentinel {COMMAND_SENTINEL}")
    payload_text = "\n".join(lines[1:]).strip()
    if not payload_text:
        raise ContractError("command payload is missing")
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"command payload is not valid JSON: {exc.msg}") from exc
    return parse_command_payload(payload)


def validate_sha256(value: Any, label: str) -> str:
    text = _expect_string(value, label)
    if not _SHA256_RE.fullmatch(text):
        raise ContractError(f"{label} must be a lowercase SHA-256 hex digest")
    return text


def validate_catalog(catalog: Any) -> list[dict[str, Any]]:
    if not isinstance(catalog, dict):
        raise ContractError("catalog must be an object")
    _expect_exact_keys(catalog, {"schema_version", "experiments"}, "catalog")
    if catalog["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise ContractError("unsupported catalog schema version")
    experiments = catalog["experiments"]
    if not isinstance(experiments, list) or not experiments:
        raise ContractError("catalog experiments must be a non-empty array")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    expected_keys = {
        "experiment_id",
        "spec_version",
        "implementation",
        "research_question",
        "decision",
        "hypothesis",
        "controls",
        "input",
        "parameters",
        "scoring",
        "success_gate",
        "stop_condition",
        "max_cases",
        "max_runtime_seconds",
    }
    for index, raw in enumerate(experiments):
        if not isinstance(raw, dict):
            raise ContractError(f"catalog experiment {index} must be an object")
        _expect_exact_keys(raw, expected_keys, f"catalog experiment {index}")
        experiment_id = _expect_string(raw["experiment_id"], "catalog experiment_id")
        if not _ID_RE.fullmatch(experiment_id):
            raise ContractError("catalog experiment_id violates id contract")
        if experiment_id in seen:
            raise ContractError(f"duplicate catalog experiment_id: {experiment_id}")
        seen.add(experiment_id)
        if raw["spec_version"] != EXPERIMENT_SPEC_VERSION:
            raise ContractError(f"unsupported spec version for {experiment_id}")
        implementation = _expect_string(raw["implementation"], "implementation")
        if not _ID_RE.fullmatch(implementation):
            raise ContractError("implementation must be a symbolic allowlisted id")
        text_fields = (
            "research_question",
            "decision",
            "hypothesis",
            "scoring",
            "success_gate",
            "stop_condition",
        )
        for key in text_fields:
            if not _expect_string(raw[key], key).strip():
                raise ContractError(f"{key} must not be empty")
        if not isinstance(raw["controls"], dict):
            raise ContractError("controls must be an object")
        input_contract = raw["input"]
        if not isinstance(input_contract, dict):
            raise ContractError("input must be an object")
        _expect_exact_keys(input_contract, {"fixture_id", "sha256"}, "experiment input")
        fixture_id = _expect_string(input_contract["fixture_id"], "fixture_id")
        if not _ID_RE.fullmatch(fixture_id):
            raise ContractError("fixture_id violates id contract")
        validate_sha256(input_contract["sha256"], "input.sha256")
        parameters = raw["parameters"]
        if not isinstance(parameters, dict):
            raise ContractError("parameters contract must be an object")
        _expect_exact_keys(parameters, {"allowed_repeat_values"}, "parameters contract")
        allowed_repeat_values = parameters["allowed_repeat_values"]
        if (
            not isinstance(allowed_repeat_values, list)
            or not allowed_repeat_values
            or any(type(item) is not int for item in allowed_repeat_values)
        ):
            raise ContractError("allowed_repeat_values must be a non-empty integer array")
        if sorted(set(allowed_repeat_values)) != allowed_repeat_values:
            raise ContractError("allowed_repeat_values must be sorted and unique")
        max_cases = raw["max_cases"]
        max_runtime = raw["max_runtime_seconds"]
        if type(max_cases) is not int or not 1 <= max_cases <= 100:
            raise ContractError("max_cases must be an integer between 1 and 100")
        if type(max_runtime) is not int or not 1 <= max_runtime <= 300:
            raise ContractError("max_runtime_seconds must be an integer between 1 and 300")
        if any(value < 1 or value > max_cases for value in allowed_repeat_values):
            raise ContractError("allowed repeat values must stay within max_cases")
        validated.append(raw)
    return validated


def validate_experiment_parameters(spec: dict[str, Any], parameters: dict[str, Any]) -> int:
    _expect_exact_keys(parameters, {"repeat"}, "experiment parameters")
    repeat = parameters["repeat"]
    if type(repeat) is not int:
        raise ContractError("repeat must be an integer")
    allowed = spec["parameters"]["allowed_repeat_values"]
    if repeat not in allowed:
        raise ContractError(f"repeat must be one of {allowed}")
    if repeat > spec["max_cases"]:
        raise ContractError("repeat exceeds max_cases")
    return repeat
