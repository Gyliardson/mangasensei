from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx

from mangasensei.config import Settings
from tests.pdf_scale.generator import generate_pdf, source_manifest
from tests.pdf_scale.raster_contract import (
    load_raster_contract,
    require_manifest_matches_frozen,
)
from tests.pdf_scale.runtime_common import (
    ComposeHarness,
    sha256_file,
    wait_until,
    write_json,
)

_RENDERER_MEMORY = 1024 * 1024 * 1024
_IMPORTER_MEMORY = 1536 * 1024 * 1024
_HEADROOM_REVIEW = 0.80


def admit(harness: ComposeHarness, *, key: str) -> tuple[str, str, float]:
    started = time.perf_counter()
    response = httpx.post(
        f"http://127.0.0.1:{harness.port}/api/v1/document-imports",
        headers={"Idempotency-Key": key},
        data={"studyLanguage": "en"},
        files={"pdf": ("pdf-pagecount-max-200.pdf", generate_pdf(), "application/pdf")},
        timeout=30.0,
    )
    assert response.status_code == 202, response.text
    admitted = response.json()["data"]
    return (
        admitted["importId"],
        admitted["capabilities"]["readDocumentImport"],
        started,
    )


def _wait_state(
    harness: ComposeHarness,
    import_id: str,
    *,
    status: str,
    timeout: float = 245.0,
) -> dict[str, Any]:
    def predicate() -> dict[str, Any] | None:
        value = harness.db_state(import_id)
        return value if value["status"] == status else None

    return wait_until(
        predicate,
        timeout=timeout,
        description=f"DocumentImport {import_id} status={status}",
        interval=0.15,
    )


def wait_terminal_cleanup(
    harness: ComposeHarness,
    import_id: str,
    *,
    status: str,
    timeout: float = 245.0,
) -> dict[str, Any]:
    def predicate() -> dict[str, Any] | None:
        value = harness.db_state(import_id)
        if value["status"] == status and value["sourceCleaned"]:
            return value
        return None

    return wait_until(
        predicate,
        timeout=timeout,
        description=f"DocumentImport {import_id} terminal cleanup",
        interval=0.15,
    )


def require_resource(
    value: dict[str, Any],
    *,
    expected_memory: int,
    role: str,
) -> None:
    assert value["configuredMemoryBytes"] == expected_memory, (role, value)
    assert value["configuredCpus"] == 1.0, (role, value)
    assert value["configuredPidsLimit"] == 64, (role, value)
    assert value["oomKilled"] is False, (role, value)
    assert value["stateStatus"] == "running", (role, value)
    events = value["memoryEvents"]
    if events is not None:
        assert events.get("oom", 0) == 0, (role, events)
        assert events.get("oom_kill", 0) == 0, (role, events)


def _require_final_graph(
    harness: ComposeHarness,
    import_id: str,
    *,
    page_count: int = 200,
) -> dict[str, Any]:
    state = harness.db_state(import_id)
    assert state["status"] == "completed"
    assert state["documents"] == 1
    assert state["pages"] == page_count
    assert state["jobs"] == page_count
    assert state["pendingJobs"] == page_count
    assert state["pageCount"] == page_count
    assert state["errorCode"] is None
    assert state["sourceCleaned"] is True
    assert harness.document_source_kind(import_id) == "pdf"

    digests = harness.page_digests(import_id)
    assert [item["ordinal"] for item in digests] == list(range(page_count))
    if page_count == 200:
        frozen = load_raster_contract()
        assert [item["sha256"] for item in digests] == [
            page["sha256"] for page in frozen["pages"]
        ]
        assert state["imageBlobs"] == 200
    return state


def require_import_capability(
    harness: ComposeHarness,
    *,
    import_id: str,
    token: str,
    expected_pages: int,
) -> None:
    response = httpx.get(
        f"http://127.0.0.1:{harness.port}/api/v1/document-imports/{import_id}",
        headers={"X-Document-Import-Token": token},
        timeout=10.0,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "completed"
    assert data["pageCount"] == expected_pages
    document = data["document"]
    assert document is not None
    document_response = httpx.get(
        f"http://127.0.0.1:{harness.port}/api/v1/documents/{document['documentId']}",
        headers={"X-Document-Token": document["capabilities"]["readDocument"]},
        timeout=10.0,
    )
    assert document_response.status_code == 200
    document_data = document_response.json()["data"]
    assert document_data["sourceKind"] == "pdf"
    assert [page["ordinal"] for page in document_data["pages"]] == list(range(expected_pages))


def probe_timings(
    harness: ComposeHarness,
    *,
    output_dir: Path,
    import_id: str,
    fence: int,
) -> dict[str, float]:
    raw_path = output_dir / "importer-probe.jsonl"
    events = harness.copy_importer_probe(raw_path)
    selected = {
        event["phase"]: float(event["elapsedSeconds"])
        for event in events
        if event.get("importId") == import_id and event.get("fencingToken") == fence
    }
    if raw_path.exists():
        raw_path.unlink()
    assert {"manifestValidation", "commit", "overall"} <= set(selected), selected
    return selected


def _clean_scenario(root: Path, source_sha: str) -> dict[str, Any]:
    output = root / "clean"
    harness = ComposeHarness(project="mangasensei_e3_clean", port=18080)
    try:
        harness.start_base()
        harness.pause("pdf-renderer")
        harness.start_importer()
        import_id, token, admitted_at = admit(harness, key="e3-clean-200-v1")
        harness.wait_request(import_id, 1)
        harness.pause("pdf-importer")

        render_started = time.perf_counter()
        harness.unpause("pdf-renderer")
        harness.wait_manifest(import_id, 1)
        render_elapsed = time.perf_counter() - render_started
        manifest = harness.read_manifest(import_id, 1)
        require_manifest_matches_frozen(manifest, import_id=import_id, fencing_token=1)
        frozen = load_raster_contract()
        assert harness.ordered_raster_sha256(import_id, 1) == frozen["orderedRasterSha256"]

        precommit = harness.db_state(import_id)
        assert precommit["documents"] == 0
        assert precommit["pages"] == 0
        assert precommit["jobs"] == 0
        assert precommit["documentId"] is None
        assert precommit["status"] == "rendering"
        assert precommit["fencingToken"] == 1
        spool_at_manifest = harness.spool_snapshot(import_id)
        assert spool_at_manifest["sourceExists"] is True
        assert spool_at_manifest["sourceBytes"] == 46_282
        assert spool_at_manifest["requestCount"] == 1
        assert spool_at_manifest["outputImportExists"] is True
        assert spool_at_manifest["outputFileCount"] == 201
        storage_before_commit = harness.storage_bytes()
        assert storage_before_commit == 0

        renderer_resource = harness.resource_snapshot("pdf-renderer")
        require_resource(renderer_resource, expected_memory=_RENDERER_MEMORY, role="renderer")

        harness.unpause("pdf-importer")
        final = wait_terminal_cleanup(harness, import_id, status="completed")
        total_elapsed = time.perf_counter() - admitted_at
        final = _require_final_graph(harness, import_id)
        require_import_capability(
            harness,
            import_id=import_id,
            token=token,
            expected_pages=200,
        )
        importer_resource = harness.resource_snapshot("pdf-importer")
        require_resource(importer_resource, expected_memory=_IMPORTER_MEMORY, role="importer")
        timings = probe_timings(
            harness,
            output_dir=output,
            import_id=import_id,
            fence=1,
        )
        spool_final = harness.spool_snapshot(import_id)
        assert spool_final["sourceExists"] is False
        assert spool_final["requestCount"] == 0
        assert spool_final["outputImportExists"] is False
        storage_after_commit = harness.storage_bytes()
        assert storage_after_commit >= frozen["aggregateRasterBytes"]

        raster_validation = {
            "rasterContract": frozen["rasterContract"],
            "aggregateRasterBytes": frozen["aggregateRasterBytes"],
            "aggregatePixels": frozen["aggregatePixels"],
            "minRasterBytes": frozen["minRasterBytes"],
            "maxRasterBytes": frozen["maxRasterBytes"],
            "orderedRasterSha256": frozen["orderedRasterSha256"],
            "pages": frozen["pages"],
            "renderer": frozen["renderer"],
        }
        scenario = {
            "schemaVersion": 1,
            "scenario": "clean-max-page",
            "repositorySourceSha": source_sha,
            "importId": import_id,
            "fencingToken": 1,
            "result": "pass",
            "rendererElapsedSeconds": render_elapsed,
            "manifestValidationElapsedSeconds": timings["manifestValidation"],
            "commitElapsedSeconds": timings["commit"],
            "coordinatorProcessElapsedSeconds": timings["overall"],
            "overallImportElapsedSeconds": total_elapsed,
            "storageBytesBeforeCommit": storage_before_commit,
            "storageBytesAfterCommit": storage_after_commit,
            "capabilityRoundTrip": True,
        }
        write_json(output / "precommit-db.json", precommit)
        write_json(output / "final-db.json", final)
        write_json(
            output / "spool-checkpoints.json",
            {"manifestCompletion": spool_at_manifest, "terminal": spool_final},
        )
        write_json(output / "renderer-resource.json", renderer_resource)
        write_json(output / "importer-resource.json", importer_resource)
        write_json(output / "raster-validation.json", raster_validation)
        write_json(output / "scenario.json", scenario)
        return {
            "scenario": scenario,
            "rendererResource": renderer_resource,
            "importerResource": importer_resource,
        }
    finally:
        harness.reset()


def _renderer_crash_scenario(root: Path, source_sha: str) -> dict[str, Any]:
    output = root / "renderer-crash"
    harness = ComposeHarness(project="mangasensei_e3_renderer_crash", port=18080)
    try:
        harness.start_base()
        harness.pause("pdf-renderer")
        harness.start_importer()
        import_id, _token, admitted_at = admit(harness, key="e3-renderer-crash-v1")
        harness.wait_request(import_id, 1)
        harness.unpause("pdf-renderer")
        harness.wait_renderer_child(timeout=10, import_id=import_id, fence=1)
        renderer_before_stop = harness.resource_snapshot("pdf-renderer")
        assert renderer_before_stop["oomKilled"] is False
        assert not harness.manifest_exists(import_id, 1)
        harness.stop_now("pdf-renderer")
        renderer_stopped = harness.stopped_state("pdf-renderer")
        assert renderer_stopped["oomKilled"] is False

        failed = wait_terminal_cleanup(harness, import_id, status="failed", timeout=30)
        assert failed["errorCode"] == "pdf_renderer_crash"
        assert failed["documents"] == 0
        assert failed["pages"] == 0
        assert failed["jobs"] == 0
        failed_spool = harness.spool_snapshot(import_id)
        assert failed_spool["sourceExists"] is False
        assert failed_spool["requestCount"] == 0
        assert failed_spool["outputImportExists"] is False

        harness.start("pdf-renderer")
        harness.wait_healthy("pdf-renderer", 90)
        fresh_id, fresh_token, _fresh_started = admit(
            harness,
            key="e3-renderer-crash-fresh-v1",
        )
        fresh_final = wait_terminal_cleanup(harness, fresh_id, status="completed")
        fresh_final = _require_final_graph(harness, fresh_id)
        require_import_capability(
            harness,
            import_id=fresh_id,
            token=fresh_token,
            expected_pages=200,
        )
        fresh_renderer = harness.resource_snapshot("pdf-renderer")
        assert fresh_renderer["oomKilled"] is False
        fresh_spool = harness.spool_snapshot(fresh_id)
        assert fresh_spool["sourceExists"] is False
        assert fresh_spool["outputImportExists"] is False

        evidence = {
            "schemaVersion": 1,
            "scenario": "renderer-crash",
            "repositorySourceSha": source_sha,
            "failedImportId": import_id,
            "failedErrorCode": failed["errorCode"],
            "zeroPartialGraph": {
                "documents": failed["documents"],
                "pages": failed["pages"],
                "jobs": failed["jobs"],
            },
            "rendererBeforeIntentionalStop": renderer_before_stop,
            "rendererAfterIntentionalStop": renderer_stopped,
            "freshImportId": fresh_id,
            "freshImportCompleted": True,
            "freshFinalGraph": fresh_final,
            "freshRendererState": fresh_renderer,
            "result": "pass",
        }
        write_json(output / "recovery-renderer-crash.json", evidence)
        return evidence
    finally:
        harness.reset()


def _importer_reclaim_scenario(root: Path, source_sha: str) -> dict[str, Any]:
    output = root / "importer-reclaim"
    harness = ComposeHarness(project="mangasensei_e3_importer_reclaim", port=18080)
    try:
        harness.start_base()
        harness.pause("pdf-renderer")
        harness.start_importer()
        import_id, token, admitted_at = admit(harness, key="e3-importer-reclaim-v1")
        harness.wait_request(import_id, 1)
        harness.pause("pdf-importer")
        harness.unpause("pdf-renderer")
        harness.wait_manifest(import_id, 1)
        manifest = harness.read_manifest(import_id, 1)
        require_manifest_matches_frozen(manifest, import_id=import_id, fencing_token=1)
        precommit = harness.db_state(import_id)
        assert precommit["documents"] == 0
        assert precommit["pages"] == 0
        assert precommit["jobs"] == 0
        assert precommit["fencingToken"] == 1
        assert precommit["status"] == "rendering"
        assert harness.manifest_exists(import_id, 1)

        harness.stop_now("pdf-importer")
        stopped = harness.stopped_state("pdf-importer")
        assert stopped["oomKilled"] is False
        harness.expire_import_lease(import_id)
        expired = harness.db_state(import_id)
        assert expired["fencingToken"] == 1
        assert expired["status"] == "rendering"

        harness.start("pdf-importer")
        harness.wait_healthy("pdf-importer", 90)

        def higher_fence() -> dict[str, Any] | None:
            state = harness.db_state(import_id)
            return state if state["fencingToken"] >= 2 else None

        reclaimed = wait_until(
            higher_fence,
            timeout=15,
            description="higher PDF import fencing token",
            interval=0.1,
        )
        assert reclaimed["fencingToken"] == 2
        final = wait_terminal_cleanup(harness, import_id, status="completed")
        final = _require_final_graph(harness, import_id)
        assert final["fencingToken"] == 2
        require_import_capability(
            harness,
            import_id=import_id,
            token=token,
            expected_pages=200,
        )
        final_spool = harness.spool_snapshot(import_id)
        assert final_spool["sourceExists"] is False
        assert final_spool["requestCount"] == 0
        assert final_spool["outputImportExists"] is False

        evidence = {
            "schemaVersion": 1,
            "scenario": "importer-reclaim",
            "repositorySourceSha": source_sha,
            "importId": import_id,
            "fenceBeforeCrash": 1,
            "fence1ManifestCompleteBeforeCrash": True,
            "precommitGraph": precommit,
            "intentionalImporterStop": stopped,
            "leaseExpiredByTestHarness": True,
            "fenceAfterReclaim": reclaimed["fencingToken"],
            "finalGraph": final,
            "staleFenceCommitted": False,
            "terminalSpool": final_spool,
            "result": "pass",
        }
        write_json(output / "recovery-importer-reclaim.json", evidence)
        return evidence
    finally:
        harness.reset()


def _assemble_result(
    root: Path,
    *,
    source_sha: str,
    clean: dict[str, Any],
    renderer_crash: dict[str, Any],
    importer_reclaim: dict[str, Any],
) -> None:
    postcommit_path = root / "recovery-postcommit-cleanup.json"
    if not postcommit_path.is_file():
        raise AssertionError("missing post-commit cleanup recovery evidence")
    postcommit = json.loads(postcommit_path.read_text(encoding="utf-8"))
    assert postcommit["result"] == "pass"
    assert postcommit["repositorySourceSha"] == source_sha

    settings = Settings(environment="test")
    renderer_resource = clean["rendererResource"]
    importer_resource = clean["importerResource"]
    renderer_ratio = renderer_resource["peakRatio"]
    importer_ratio = importer_resource["peakRatio"]
    review_required = any(
        ratio is not None and ratio >= _HEADROOM_REVIEW
        for ratio in (renderer_ratio, importer_ratio)
    )
    evidence_files = []
    for path in sorted(root.rglob("*.json")):
        if path.name == "result-manifest.json":
            continue
        evidence_files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    required = {
        "workload-manifest.json",
        "clean/precommit-db.json",
        "clean/final-db.json",
        "clean/spool-checkpoints.json",
        "clean/renderer-resource.json",
        "clean/importer-resource.json",
        "clean/raster-validation.json",
        "clean/scenario.json",
        "renderer-crash/recovery-renderer-crash.json",
        "importer-reclaim/recovery-importer-reclaim.json",
        "recovery-postcommit-cleanup.json",
    }
    present = {item["path"] for item in evidence_files}
    missing = required - present
    if missing:
        raise AssertionError(f"missing load-bearing E3 evidence: {sorted(missing)}")

    frozen = load_raster_contract()
    result = {
        "schemaVersion": 1,
        "repositorySourceSha": source_sha,
        "workload": source_manifest(generate_pdf()),
        "rasterCalibrationSourceSha": frozen["calibrationSourceSha"],
        "rasterContract": {
            "version": frozen["rasterContract"],
            "aggregateRasterBytes": frozen["aggregateRasterBytes"],
            "aggregatePixels": frozen["aggregatePixels"],
            "orderedRasterSha256": frozen["orderedRasterSha256"],
            "renderer": frozen["renderer"],
        },
        "configuredLimits": {
            "pdfSourceBytes": settings.max_pdf_bytes,
            "pdfPages": settings.max_pdf_pages,
            "pageRasterBytes": settings.max_upload_bytes,
            "pagePixels": settings.max_image_pixels,
            "maxSide": settings.max_image_side,
            "aggregateRasterBytes": settings.max_pdf_raster_bytes,
            "aggregatePixels": settings.max_document_pixels,
            "spoolBytes": settings.max_pdf_spool_bytes,
            "rendererTimeoutSeconds": settings.pdf_renderer_timeout_seconds,
            "importerLeaseSeconds": settings.pdf_import_lease_seconds,
            "sourceTtlSeconds": settings.pdf_source_ttl_seconds,
            "rendererMemoryBytes": _RENDERER_MEMORY,
            "importerMemoryBytes": _IMPORTER_MEMORY,
            "cpu": 1.0,
            "pids": 64,
        },
        "headroomReviewThreshold": _HEADROOM_REVIEW,
        "headroomReviewRequired": review_required,
        "scenarios": {
            "cleanMaxPage": clean["scenario"]["result"],
            "rendererCrash": renderer_crash["result"],
            "importerReclaim": importer_reclaim["result"],
            "postCommitCleanup": postcommit["result"],
        },
        "evidenceFiles": evidence_files,
    }
    write_json(root / "result-manifest.json", result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if len(args.source_sha) != 40:
        raise AssertionError("repository source SHA must be a full commit SHA")
    args.root.mkdir(parents=True, exist_ok=True)
    write_json(args.root / "workload-manifest.json", source_manifest(generate_pdf()))
    clean = _clean_scenario(args.root, args.source_sha)
    renderer_crash = _renderer_crash_scenario(args.root, args.source_sha)
    importer_reclaim = _importer_reclaim_scenario(args.root, args.source_sha)
    _assemble_result(
        args.root,
        source_sha=args.source_sha,
        clean=clean,
        renderer_crash=renderer_crash,
        importer_reclaim=importer_reclaim,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
