"""Isolated API/worker runtime for the deterministic Slice E1 browser gate."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import multiprocessing
import os
import shutil
import time
from pathlib import Path

import uvicorn
from sqlalchemy import func, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mangasensei.api.app import create_app
from mangasensei.cli import main as cli_main
from mangasensei.config import Settings
from mangasensei.infrastructure.database.document_models import DocumentRecord
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.operational_models import RateLimitBucketRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from tests.large_document.db_diagnostics import collect


class RequestMetricsMiddleware:
    """Record non-secret request provenance without persisting capability values."""

    def __init__(self, app: ASGIApp, output: Path) -> None:
        self._app = app
        self._output = output

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        status = 500
        started = time.perf_counter()
        header_names = {name.lower() for name, _value in scope["headers"]}
        safe_headers = {
            name.lower(): value
            for name, value in scope["headers"]
            if name.lower() in {b"sec-fetch-dest", b"sec-fetch-mode", b"sec-fetch-site"}
        }

        async def tracked_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, tracked_send)
        finally:
            record = {
                "method": scope["method"],
                "path": scope["path"],
                "status": status,
                "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
                "documentTokenHeaderPresent": b"x-document-token" in header_names,
                "secFetchDest": safe_headers.get(b"sec-fetch-dest", b"").decode(
                    "ascii", errors="replace"
                ),
                "secFetchMode": safe_headers.get(b"sec-fetch-mode", b"").decode(
                    "ascii", errors="replace"
                ),
                "secFetchSite": safe_headers.get(b"sec-fetch-site", b"").decode(
                    "ascii", errors="replace"
                ),
            }
            with self._output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, sort_keys=True) + "\n")


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def _assert_fresh_database(settings: Settings) -> None:
    engine, sessions = create_database(settings.require_database_url())
    try:
        async with sessions() as session:
            counts = {
                "documents": int(
                    await session.scalar(select(func.count()).select_from(DocumentRecord)) or 0
                ),
                "pages": int(
                    await session.scalar(select(func.count()).select_from(PageRecord)) or 0
                ),
                "jobs": int(
                    await session.scalar(select(func.count()).select_from(JobRecord)) or 0
                ),
                "imageBlobs": int(
                    await session.scalar(select(func.count()).select_from(ImageBlobRecord)) or 0
                ),
                "rateLimitBuckets": int(
                    await session.scalar(
                        select(func.count()).select_from(RateLimitBucketRecord)
                    )
                    or 0
                ),
            }
    finally:
        await engine.dispose()
    if any(counts.values()):
        raise RuntimeError(f"large-document runtime requires fresh PostgreSQL state: {counts!r}")


def _worker_process(log_path: str) -> None:
    from tests.large_document.worker import main as worker_main

    path = Path(log_path)
    with (
        path.open("w", encoding="utf-8") as stream,
        contextlib.redirect_stdout(stream),
        contextlib.redirect_stderr(stream),
    ):
        asyncio.run(worker_main())


async def _wait_for_marker(path: Path, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if await asyncio.to_thread(path.is_file):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("browser did not persist the document marker after admission")


async def _gate_worker(
    *,
    root: Path,
    marker: Path,
    server: uvicorn.Server,
) -> None:
    worker: multiprocessing.Process | None = None
    try:
        await _wait_for_marker(marker, timeout_seconds=120)
        initial = await collect("initial", marker)
        _write_json(root / "db-initial.json", initial)
        context = multiprocessing.get_context("spawn")
        worker = context.Process(
            target=_worker_process,
            args=(str(root / "worker.log"),),
            name="large-document-e1-worker",
        )
        worker.start()
        while not server.should_exit:
            if worker.exitcode is not None:
                raise RuntimeError(
                    f"large-document worker exited unexpectedly with code {worker.exitcode}"
                )
            await asyncio.sleep(0.1)
    except Exception:
        server.should_exit = True
        raise
    finally:
        if worker is not None and worker.is_alive():
            worker.terminate()
            worker.join(timeout=5)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=5)


async def _serve(root: Path) -> None:
    settings = Settings(_env_file=None)
    if settings.api_rate_limit_per_minute != 120:
        raise RuntimeError("large-document API must use the unchanged 120/min default")
    await _assert_fresh_database(settings)

    marker = Path(_required_env("MANGASENSEI_LARGE_DOCUMENT_MARKER"))
    await asyncio.to_thread(marker.unlink, missing_ok=True)
    metrics_path = root / "runtime-http.jsonl"
    await asyncio.to_thread(metrics_path.unlink, missing_ok=True)
    app = RequestMetricsMiddleware(create_app(settings), metrics_path)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8000,
            log_level="info",
            proxy_headers=False,
            server_header=False,
        )
    )
    gate = asyncio.create_task(_gate_worker(root=root, marker=marker, server=server))
    gate_error: BaseException | None = None
    try:
        await server.serve()
    finally:
        if not gate.done():
            gate.cancel()
        try:
            await gate
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            gate_error = exc
    if gate_error is not None:
        raise gate_error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)

    storage = Path(_required_env("MANGASENSEI_STORAGE_ROOT"))
    shutil.rmtree(storage, ignore_errors=True)
    storage.mkdir(parents=True, exist_ok=True)
    marker = Path(_required_env("MANGASENSEI_LARGE_DOCUMENT_MARKER"))
    marker.unlink(missing_ok=True)
    for artifact in ("db-initial.json", "worker.log", "runtime-http.jsonl"):
        (root / artifact).unlink(missing_ok=True)

    if cli_main(["migrate"]) != 0:
        raise RuntimeError("database migration failed")
    asyncio.run(_serve(root))


if __name__ == "__main__":
    main()
