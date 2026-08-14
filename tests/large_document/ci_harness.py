"""Run the isolated Slice E1 runtime and browser scenario on a hosted CI worker."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO

from mangasensei.config import Settings
from tests.large_document.db_diagnostics import collect
from tests.large_document.generator import write_workload


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _python_cli(*args: str) -> list[str]:
    literal = repr(list(args))
    code = f"from mangasensei.cli import main; raise SystemExit(main({literal}))"
    return [sys.executable, "-c", code]


def _wait_ready(deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/ready", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise TimeoutError("dedicated API did not become ready")


def _wait_marker(path: Path, deadline: float) -> None:
    while time.monotonic() < deadline:
        if path.is_file():
            return
        time.sleep(0.05)
    raise TimeoutError("browser did not persist the document marker after admission")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _terminate(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _open_log(path: Path) -> IO[bytes]:
    return path.open("wb")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    input_dir = Path(_required_env("MANGASENSEI_LARGE_DOCUMENT_INPUT"))
    marker = Path(_required_env("MANGASENSEI_LARGE_DOCUMENT_MARKER"))
    storage = Path(_required_env("MANGASENSEI_STORAGE_ROOT"))

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/integration/test_document_large_harness.py",
            "tests/integration/test_document_scale.py",
            "tests/integration/test_queue_fairness_characterization.py",
        ],
        check=True,
    )
    shutil.rmtree(input_dir, ignore_errors=True)
    write_workload(input_dir)
    shutil.rmtree(storage, ignore_errors=True)
    storage.mkdir(parents=True, exist_ok=True)
    settings = Settings(_env_file=None)
    if settings.api_rate_limit_per_minute != 120:
        raise RuntimeError("large-document API must use the unchanged 120/min default")
    subprocess.run(_python_cli("migrate"), check=True)

    api: subprocess.Popen[bytes] | None = None
    worker: subprocess.Popen[bytes] | None = None
    browser: subprocess.Popen[bytes] | None = None
    with _open_log(root / "api.log") as api_log, _open_log(root / "worker.log") as worker_log:
        try:
            api = subprocess.Popen(
                _python_cli("api", "--host", "127.0.0.1", "--port", "8000"),
                stdout=api_log,
                stderr=subprocess.STDOUT,
            )
            _wait_ready(time.monotonic() + 30)
            browser = subprocess.Popen(
                [
                    "npx",
                    "--no-install",
                    "playwright",
                    "test",
                    "--config",
                    "frontend/playwright.large-document.config.ts",
                ]
            )
            _wait_marker(marker, time.monotonic() + 120)
            initial = asyncio.run(collect("initial", marker))
            _write_json(root / "db-initial.json", initial)
            worker = subprocess.Popen(
                [sys.executable, "-m", "tests.large_document.worker"],
                stdout=worker_log,
                stderr=subprocess.STDOUT,
            )
            browser_status = browser.wait(timeout=150)
            if browser_status != 0:
                raise RuntimeError(f"large-document Playwright exited with {browser_status}")
            final = asyncio.run(collect("final", marker))
            _write_json(root / "db-final.json", final)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "tests.large_document.ci_metrics",
                    "--root",
                    str(root),
                    "--source-sha",
                    args.source_sha,
                ],
                check=True,
            )
        finally:
            _terminate(browser)
            _terminate(worker)
            _terminate(api)


if __name__ == "__main__":
    main()
