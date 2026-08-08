from __future__ import annotations

from dataclasses import dataclass

import pytest

from mangasensei.cli import build_parser, main
from mangasensei.runtime import run_retention_loop, run_worker_loop


@dataclass
class WorkerFixture:
    calls: int = 0

    async def run_once(self) -> bool:
        self.calls += 1
        return True


@dataclass
class RetentionFixture:
    calls: int = 0

    async def run_once(self) -> int:
        self.calls += 1
        return 2


def test_cli_parser_supports_all_operational_commands() -> None:
    parser = build_parser()

    assert parser.parse_args(["api", "--port", "9000"]).port == 9000
    assert parser.parse_args(["worker", "--once"]).once
    assert parser.parse_args(["retention", "--once"]).once
    assert parser.parse_args(["models", "download"]).models_command == "download"
    assert parser.parse_args(["models", "verify"]).models_command == "verify"
    assert parser.parse_args(["jmdict", "download"]).jmdict_command == "download"
    assert parser.parse_args(["jmdict", "verify"]).jmdict_command == "verify"
    assert parser.parse_args(["migrate"]).command == "migrate"


def test_cli_artifact_commands_do_not_require_database_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    from pathlib import Path

    for variable in (
        "MANGASENSEI_DATABASE_URL",
        "MANGASENSEI_CAPABILITY_PEPPERS",
        "MANGASENSEI_JMDICT_PATH",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("MANGASENSEI_JMDICT_PATH", str(Path(tmp_path) / "missing.json"))  # type: ignore[arg-type]

    code = main(["jmdict", "verify"])

    captured = capsys.readouterr()
    assert code == 2
    assert "ValidationError" not in captured.err


@pytest.mark.asyncio
async def test_worker_loop_once_processes_one_cycle() -> None:
    worker = WorkerFixture()

    await run_worker_loop(worker, poll_seconds=0.01, once=True)

    assert worker.calls == 1


@pytest.mark.asyncio
async def test_retention_loop_once_processes_one_cycle() -> None:
    janitor = RetentionFixture()

    await run_retention_loop(janitor, poll_seconds=0.01, once=True)

    assert janitor.calls == 1
