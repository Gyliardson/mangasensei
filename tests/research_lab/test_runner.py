from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.research_lab import runner as research_runner
from scripts.research_lab.contracts import (
    EXPERIMENT_SPEC_VERSION,
    ContractError,
    ResearchCommand,
    sha256_file,
)
from scripts.research_lab.runner import execute_command, write_evidence

BASELINE = "5" * 40


def _command(**overrides: object) -> ResearchCommand:
    values: dict[str, object] = {
        "command_id": "smoke-command-001",
        "experiment_id": "framework-smoke-v1",
        "baseline_sha": BASELINE,
        "spec_version": EXPERIMENT_SPEC_VERSION,
        "parameters": {"repeat": 2},
    }
    values.update(overrides)
    return ResearchCommand(**values)  # type: ignore[arg-type]


def test_framework_smoke_result_is_deterministic() -> None:
    first, _ = execute_command(_command(), expected_baseline=BASELINE, runtime_context={})
    second, _ = execute_command(_command(), expected_baseline=BASELINE, runtime_context={})
    assert first == second
    assert first["decision"] == "PASS"
    assert first["counts"] == {
        "requested_cases": 2,
        "completed_cases": 2,
        "positive_matches": 2,
        "negative_distinct": 2,
        "invalid_cases": 0,
    }


@pytest.mark.parametrize(
    ("command", "baseline"),
    [
        (_command(experiment_id="unlisted-experiment-v1"), BASELINE),
        (_command(parameters={"repeat": 4}), BASELINE),
        (_command(parameters={"repeat": 2, "url": "https://example.invalid"}), BASELINE),
        (_command(parameters={"repeat": 2, "shell": "rm -rf /"}), BASELINE),
        (_command(), "6" * 40),
    ],
)
def test_runner_fails_closed_on_unallowlisted_or_stale_inputs(
    command: ResearchCommand, baseline: str
) -> None:
    with pytest.raises(ContractError):
        execute_command(command, expected_baseline=baseline, runtime_context={})


def test_runner_enforces_frozen_runtime_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    timestamps = iter((0, 11_000_000_000))
    monkeypatch.setattr(research_runner.time, "monotonic_ns", lambda: next(timestamps))
    with pytest.raises(ContractError, match="max_runtime_seconds"):
        execute_command(_command(parameters={"repeat": 1}), expected_baseline=BASELINE)


def test_evidence_bundle_has_machine_verifiable_hashes(tmp_path: Path) -> None:
    result, provenance = execute_command(
        _command(parameters={"repeat": 1}),
        expected_baseline=BASELINE,
        runtime_context={"repository": "Gyliardson/mangasensei", "run_id": "123"},
    )
    output_dir = tmp_path / "evidence"
    hashes = write_evidence(output_dir, result, provenance)
    assert set(hashes) == {"results.json", "provenance.json", "checksums.sha256"}
    for name, digest in hashes.items():
        assert sha256_file(output_dir / name) == digest
    checksum_lines = (output_dir / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    assert checksum_lines == sorted(checksum_lines, key=lambda line: line.split("  ", 1)[1])
    result_payload = json.loads(
        (output_dir / "results.json").read_text(encoding="utf-8")
    )
    assert result_payload["decision"] == "PASS"
