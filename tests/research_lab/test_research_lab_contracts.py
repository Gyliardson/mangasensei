from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.research_lab.contracts import (
    COMMAND_SENTINEL,
    EXPERIMENT_SPEC_VERSION,
    ContractError,
    parse_command_comment,
    sha256_file,
    validate_catalog,
)

BASELINE = "5" * 40


def _comment(**overrides: object) -> str:
    payload: dict[str, object] = {
        "command_id": "smoke-command-001",
        "experiment_id": "framework-smoke-v1",
        "baseline_sha": BASELINE,
        "spec_version": EXPERIMENT_SPEC_VERSION,
        "parameters": {"repeat": 2},
    }
    payload.update(overrides)
    return f"{COMMAND_SENTINEL}\n{json.dumps(payload)}"


def test_parse_command_accepts_exact_v1_contract() -> None:
    command = parse_command_comment(_comment())
    assert command.command_id == "smoke-command-001"
    assert command.parameters == {"repeat": 2}


@pytest.mark.parametrize(
    "body",
    [
        "not-a-command\n{}",
        f"{COMMAND_SENTINEL}\n[]",
        _comment(extra="forbidden"),
        _comment(baseline_sha="main"),
        _comment(command_id="UPPERCASE"),
        _comment(spec_version="v2"),
        _comment(parameters="repeat=2"),
    ],
)
def test_parse_command_rejects_non_contract_payloads(body: str) -> None:
    with pytest.raises(ContractError):
        parse_command_comment(body)


def test_catalog_is_strict_and_fixture_checksum_is_frozen() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads((root / "scripts/research_lab/catalog.json").read_text(encoding="utf-8"))
    experiments = validate_catalog(catalog)
    assert [entry["experiment_id"] for entry in experiments] == ["framework-smoke-v1"]
    fixture = root / "scripts/research_lab/fixtures/framework-smoke-v1.txt"
    assert sha256_file(fixture) == experiments[0]["input"]["sha256"]


def test_catalog_rejects_unknown_fields() -> None:
    root = Path(__file__).resolve().parents[2]
    catalog = json.loads((root / "scripts/research_lab/catalog.json").read_text(encoding="utf-8"))
    catalog["experiments"][0]["arbitrary_shell"] = "bash -c anything"
    with pytest.raises(ContractError):
        validate_catalog(catalog)


def test_versioned_schema_files_are_strict_json_objects() -> None:
    root = Path(__file__).resolve().parents[2]
    schema_dir = root / "scripts/research_lab/schemas"
    names = {
        "command-v1.schema.json",
        "experiment-spec-v1.schema.json",
        "result-v1.schema.json",
        "provenance-v1.schema.json",
    }
    assert {path.name for path in schema_dir.glob("*.json")} == names
    for name in names:
        schema = json.loads((schema_dir / name).read_text(encoding="utf-8"))
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
