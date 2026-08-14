from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import httpx

_DOCKER = shutil.which("docker")
if _DOCKER is None:
    raise RuntimeError("docker executable is required for E3 runtime evidence")

_COMPOSE_FILES = ("compose.yaml", "tests/pdf_scale/compose.e3.yml")
_T = TypeVar("_T")


class ComposeHarness:
    def __init__(self, *, project: str, port: int = 18080) -> None:
        self.project = project
        self.port = port
        self.env = os.environ.copy()
        self.env.update(
            {
                "COMPOSE_PROJECT_NAME": project,
                "POSTGRES_PASSWORD": "pdf-e3-postgres",
                "POSTGRES_PORT": "55432",
                "MANGASENSEI_PORT": str(port),
                "MANGASENSEI_CAPABILITY_PEPPERS": (
                    '["pdf-e3-capability-pepper-000000000000000001"]'
                ),
                "MANGASENSEI_VERSION": "dev",
            }
        )

    def compose(
        self,
        *args: str,
        capture: bool = False,
        check: bool = True,
        input_text: str | None = None,
    ) -> str:
        command = [_DOCKER, "compose"]
        for filename in _COMPOSE_FILES:
            command.extend(("-f", filename))
        command.extend(args)
        completed = subprocess.run(  # noqa: S603
            command,
            env=self.env,
            check=check,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        return completed.stdout.strip() if capture else ""

    def docker(
        self,
        *args: str,
        capture: bool = False,
        check: bool = True,
    ) -> str:
        completed = subprocess.run(  # noqa: S603
            [_DOCKER, *args],
            env=self.env,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        return completed.stdout.strip() if capture else ""

    def reset(self) -> None:
        self.compose("down", "-v", "--remove-orphans", check=False)

    def start_base(self) -> None:
        self.reset()
        self.compose("up", "-d", "--no-build", "api", "pdf-renderer")
        self.wait_healthy("postgres", 90)
        self.wait_healthy("pdf-spool-init", 45)
        self.wait_healthy("api", 90)
        self.wait_healthy("pdf-renderer", 90)
        self.wait_http_health(60)

    def start_importer(self) -> None:
        self.compose("up", "-d", "--no-deps", "--no-build", "pdf-importer")
        self.wait_healthy("pdf-importer", 90)

    def wait_healthy(self, service: str, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            container_id = self.service_id(service, allow_missing=True)
            if container_id:
                status = self.docker(
                    "inspect",
                    "--format",
                    "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                    container_id,
                    capture=True,
                    check=False,
                )
                if status in {"healthy", "exited"}:
                    return
            time.sleep(0.25)
        raise AssertionError(f"{service} did not become healthy within {timeout}s")

    def wait_http_health(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        url = f"http://127.0.0.1:{self.port}/health"
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url, timeout=1.0)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise AssertionError("API did not become healthy")

    def service_id(self, service: str, *, allow_missing: bool = False) -> str:
        container_id = self.compose("ps", "-a", "-q", service, capture=True, check=False)
        if not container_id and not allow_missing:
            raise AssertionError(f"missing container for {service}")
        return container_id

    def pause(self, service: str) -> None:
        self.compose("pause", service)

    def unpause(self, service: str) -> None:
        self.compose("unpause", service)

    def stop_now(self, service: str) -> None:
        self.compose("stop", "-t", "0", service)

    def start(self, service: str) -> None:
        self.docker("start", self.service_id(service))

    def wait_renderer_child(self, *, timeout: float, import_id: str, fence: int) -> None:
        deadline = time.monotonic() + timeout
        container_id = self.service_id("pdf-renderer")
        while time.monotonic() < deadline:
            if self.manifest_exists(import_id, fence):
                raise AssertionError(
                    "renderer finished before crash coordination captured child process"
                )
            top = self.docker(
                "top",
                container_id,
                "-eo",
                "pid,comm,args",
                capture=True,
                check=False,
            )
            if len([line for line in top.splitlines() if line.strip()]) >= 3:
                return
            time.sleep(0.02)
        raise AssertionError("renderer child process was not observed")

    def wait_request(self, import_id: str, fence: int, timeout: float = 10.0) -> None:
        path = f"/app/var/pdf-spool/requests/{import_id}.{fence}.request.json"
        self._wait_anchor_path(path, exists=True, timeout=timeout)

    def wait_manifest(self, import_id: str, fence: int, timeout: float = 190.0) -> None:
        path = f"/app/var/pdf-renderer-output/imports/{import_id}/attempt-{fence}/manifest.json"
        self._wait_anchor_path(path, exists=True, timeout=timeout)

    def manifest_exists(self, import_id: str, fence: int) -> bool:
        path = f"/app/var/pdf-renderer-output/imports/{import_id}/attempt-{fence}/manifest.json"
        script = f"from pathlib import Path; print('1' if Path({path!r}).is_file() else '0')"
        return (
            self.compose(
                "exec",
                "-T",
                "pdf-spool-init",
                "python",
                "-c",
                script,
                capture=True,
                check=False,
            )
            == "1"
        )

    def _wait_anchor_path(self, path: str, *, exists: bool, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        script = f"from pathlib import Path; print('1' if Path({path!r}).is_file() else '0')"
        while time.monotonic() < deadline:
            value = self.compose(
                "exec",
                "-T",
                "pdf-spool-init",
                "python",
                "-c",
                script,
                capture=True,
                check=False,
            )
            if (value == "1") is exists:
                return
            time.sleep(0.05)
        raise AssertionError(f"spool path condition not reached: {path}")

    def read_manifest(self, import_id: str, fence: int) -> dict[str, Any]:
        path = f"/app/var/pdf-renderer-output/imports/{import_id}/attempt-{fence}/manifest.json"
        script = f"from pathlib import Path; print(Path({path!r}).read_text())"
        raw = self.compose(
            "exec",
            "-T",
            "pdf-spool-init",
            "python",
            "-c",
            script,
            capture=True,
        )
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AssertionError("manifest was not a JSON object")
        return value

    def spool_snapshot(self, import_id: str) -> dict[str, Any]:
        script = r'''
import json
from pathlib import Path
import sys

import_id = sys.argv[1]
input_root = Path("/app/var/pdf-spool")
output_root = Path("/app/var/pdf-renderer-output")
source = input_root / "imports" / import_id / "source.pdf"
requests = list((input_root / "requests").glob(f"{import_id}.*.request.json"))
output_import = output_root / "imports" / import_id


def total(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


print(json.dumps({
    "sourceExists": source.is_file(),
    "sourceBytes": source.stat().st_size if source.is_file() else 0,
    "requestCount": len(requests),
    "requestBytes": sum(path.stat().st_size for path in requests),
    "outputImportExists": output_import.is_dir(),
    "outputImportBytes": total(output_import),
    "outputFileCount": (
        sum(1 for item in output_import.rglob("*") if item.is_file())
        if output_import.is_dir() else 0
    ),
}))
'''
        raw = self.compose(
            "exec",
            "-T",
            "pdf-spool-init",
            "python",
            "-c",
            script,
            import_id,
            capture=True,
        )
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AssertionError("spool snapshot was not a JSON object")
        return value

    def spool_attempt_summary(self, import_id: str) -> dict[str, Any]:
        script = r'''
import json
from pathlib import Path
import sys

import_id = sys.argv[1]
input_root = Path("/app/var/pdf-spool")
output_root = Path("/app/var/pdf-renderer-output")
requests = sorted(path.name for path in (input_root / "requests").glob(f"{import_id}.*.request.json"))
output_import = output_root / "imports" / import_id
results = []
if output_import.is_dir():
    for attempt in sorted(output_import.glob("attempt-*"), key=lambda path: path.name):
        if not attempt.is_dir():
            continue
        results.append({
            "attempt": attempt.name,
            "manifest": (attempt / "manifest.json").is_file(),
            "failure": (attempt / "failure.json").is_file(),
            "pageFiles": sum(1 for path in attempt.glob("page-*.png") if path.is_file()),
        })
print(json.dumps({"requests": requests, "attempts": results}))
'''
        raw = self.compose(
            "exec",
            "-T",
            "pdf-spool-init",
            "python",
            "-c",
            script,
            import_id,
            capture=True,
            check=False,
        )
        value = json.loads(raw or "{}")
        if not isinstance(value, dict):
            raise AssertionError("spool attempt summary was not a JSON object")
        return value

    def ordered_raster_sha256(self, import_id: str, fence: int) -> str:
        path = f"/app/var/pdf-renderer-output/imports/{import_id}/attempt-{fence}"
        script = r'''
import hashlib
from pathlib import Path
import sys

root = Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(root.glob("page-*.png")):
    digest.update(path.read_bytes())
print(digest.hexdigest())
'''
        return self.compose(
            "exec",
            "-T",
            "pdf-spool-init",
            "python",
            "-c",
            script,
            path,
            capture=True,
        )

    def db_state(self, import_id: str) -> dict[str, Any]:
        sql = """
SELECT json_build_object(
  'documents', (SELECT count(*) FROM mangasensei.documents),
  'pages', (SELECT count(*) FROM mangasensei.pages),
  'jobs', (SELECT count(*) FROM mangasensei.jobs),
  'pendingJobs', (SELECT count(*) FROM mangasensei.jobs WHERE status = 'pending'),
  'imageBlobs', (SELECT count(*) FROM mangasensei.image_blobs),
  'status', status,
  'documentId', document_id,
  'fencingToken', fencing_token,
  'pageCount', page_count,
  'errorCode', error_code,
  'sourceCleaned', source_cleaned_at IS NOT NULL
)::text
FROM mangasensei.document_imports
WHERE public_id = :'import_id'::uuid;
"""
        raw = self.psql(sql, variables={"import_id": self._uuid_text(import_id)})
        if not raw:
            raise AssertionError(f"missing DocumentImport {import_id}")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise AssertionError("database state was not a JSON object")
        return value

    def page_digests(self, import_id: str) -> list[dict[str, Any]]:
        sql = """
SELECT COALESCE(
  json_agg(
    json_build_object(
      'ordinal', p.ordinal,
      'sha256', encode(p.request_digest, 'hex')
    )
    ORDER BY p.ordinal
  ),
  '[]'::json
)::text
FROM mangasensei.pages p
JOIN mangasensei.documents d ON d.id = p.document_id
WHERE d.id = (
  SELECT document_id FROM mangasensei.document_imports
  WHERE public_id = :'import_id'::uuid
);
"""
        value = json.loads(
            self.psql(sql, variables={"import_id": self._uuid_text(import_id)})
        )
        if not isinstance(value, list):
            raise AssertionError("page digest query was not a list")
        return value

    def image_blob_summary(self, import_id: str) -> dict[str, Any]:
        sql = """
SELECT json_build_object(
  'count', count(*),
  'totalBytes', COALESCE(sum(b.byte_size), 0),
  'ready', count(*) FILTER (WHERE b.state = 'ready'),
  'width80Height120', count(*) FILTER (WHERE b.width = 80 AND b.height = 120),
  'png', count(*) FILTER (WHERE b.media_type = 'image/png')
)::text
FROM mangasensei.image_blobs b
JOIN mangasensei.pages p ON p.image_blob_id = b.id
WHERE p.document_id = (
  SELECT document_id FROM mangasensei.document_imports
  WHERE public_id = :'import_id'::uuid
);
"""
        value = json.loads(
            self.psql(sql, variables={"import_id": self._uuid_text(import_id)})
        )
        if not isinstance(value, dict):
            raise AssertionError("image blob summary was not a JSON object")
        return value

    def document_source_kind(self, import_id: str) -> str:
        sql = """
SELECT d.source_kind
FROM mangasensei.documents d
WHERE d.id = (
  SELECT document_id FROM mangasensei.document_imports
  WHERE public_id = :'import_id'::uuid
);
"""
        return self.psql(sql, variables={"import_id": self._uuid_text(import_id)})

    def psql(self, sql: str, *, variables: dict[str, str] | None = None) -> str:
        command = [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "mangasensei",
            "-d",
            "mangasensei",
            "-tA",
        ]
        for key, value in sorted((variables or {}).items()):
            command.extend(("-v", f"{key}={value}"))
        return self.compose(*command, capture=True, input_text=sql)

    def expire_import_lease(self, import_id: str) -> None:
        sql = """
UPDATE mangasensei.document_imports
SET lease_until = CURRENT_TIMESTAMP - INTERVAL '1 second'
WHERE public_id = :'import_id'::uuid
  AND status = 'rendering';
"""
        self.psql(sql, variables={"import_id": self._uuid_text(import_id)})

    def storage_bytes(self) -> int:
        script = (
            "from pathlib import Path; "
            "r=Path('/app/var/storage'); "
            "print(sum(p.stat().st_size for p in r.rglob('*') if p.is_file()))"
        )
        raw = self.compose(
            "exec",
            "-T",
            "api",
            "python",
            "-c",
            script,
            capture=True,
        )
        return int(raw)

    def resource_snapshot(self, service: str) -> dict[str, Any]:
        container_id = self.service_id(service)
        inspect = json.loads(self.docker("inspect", container_id, capture=True))[0]
        host_config = inspect["HostConfig"]
        state = inspect["State"]
        peak = self._cgroup_int(service, "memory.peak")
        current = self._cgroup_int(service, "memory.current")
        events_raw = self._cgroup_text(service, "memory.events")
        events: dict[str, int] | None = None
        if events_raw is not None:
            events = {}
            for line in events_raw.splitlines():
                key, value = line.split()
                events[key] = int(value)
        limit = int(host_config.get("Memory") or 0)
        return {
            "containerId": container_id,
            "configuredMemoryBytes": limit,
            "configuredCpus": float(host_config.get("NanoCpus") or 0) / 1_000_000_000,
            "configuredPidsLimit": int(host_config.get("PidsLimit") or 0),
            "memoryPeakBytes": peak,
            "memoryCurrentBytes": current,
            "memoryEvents": events,
            "peakRatio": (peak / limit) if peak is not None and limit > 0 else None,
            "oomKilled": bool(state.get("OOMKilled")),
            "stateStatus": state.get("Status"),
            "exitCode": int(state.get("ExitCode") or 0),
            "restartCount": int(inspect.get("RestartCount") or 0),
        }

    def stopped_state(self, service: str) -> dict[str, Any]:
        container_id = self.service_id(service)
        inspect = json.loads(self.docker("inspect", container_id, capture=True))[0]
        state = inspect["State"]
        return {
            "containerId": container_id,
            "oomKilled": bool(state.get("OOMKilled")),
            "stateStatus": state.get("Status"),
            "exitCode": int(state.get("ExitCode") or 0),
            "restartCount": int(inspect.get("RestartCount") or 0),
        }

    def _cgroup_int(self, service: str, filename: str) -> int | None:
        raw = self._cgroup_text(service, filename)
        if raw is None or not raw.isdecimal():
            return None
        return int(raw)

    def _cgroup_text(self, service: str, filename: str) -> str | None:
        command = (
            f"from pathlib import Path; p=Path('/sys/fs/cgroup/{filename}'); "
            "print(p.read_text().strip() if p.is_file() else '')"
        )
        raw = self.compose(
            "exec",
            "-T",
            service,
            "python",
            "-c",
            command,
            capture=True,
            check=False,
        )
        return raw or None

    def copy_importer_probe(self, destination: Path) -> list[dict[str, Any]]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "from pathlib import Path; "
            "p=Path('/app/var/pdf-spool/e3-importer-probe.jsonl'); "
            "print(p.read_text() if p.is_file() else '', end='')"
        )
        raw = self.compose(
            "exec",
            "-T",
            "pdf-importer",
            "python",
            "-c",
            script,
            capture=True,
            check=False,
        )
        if not raw:
            return []
        destination.write_text(raw + "\n", encoding="utf-8")
        events = []
        for line in raw.splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        return events

    @staticmethod
    def _uuid_text(value: str) -> str:
        return str(UUID(value))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wait_until(
    predicate: Callable[[], _T | None],
    *,
    timeout: float,
    description: str,
    interval: float = 0.1,
) -> _T:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timeout waiting for {description}")
