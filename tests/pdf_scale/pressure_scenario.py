from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from tests.pdf_scale.generator import EXPECTED_SOURCE_SHA256, generate_pdf, source_manifest
from tests.pdf_scale.pressure_producer import (
    AGGREGATE_BYTES,
    AGGREGATE_PIXELS,
    PAGE_BYTES,
    PAGE_COUNT,
    PROFILE_NAME,
)
from tests.pdf_scale.runtime_common import (
    ComposeHarness,
    sha256_file,
    wait_until,
    write_json,
)
from tests.pdf_scale.scale_scenarios import (
    admit,
    probe_timings,
    require_import_capability,
    require_resource,
    wait_terminal_cleanup,
)

_HEADROOM_REVIEW = 0.80
_PRESSURE_IMPORTER_MEMORY = 1536 * 1024 * 1024


def _run_producer(harness: ComposeHarness) -> dict[str, Any]:
    source = Path.cwd() / "tests" / "pdf_scale"
    raw = harness.compose(
        "run",
        "--rm",
        "--no-deps",
        "-v",
        f"{source}:/e3:ro",
        "pdf-renderer",
        "python",
        "/e3/pressure_producer.py",
        "--source-sha",
        EXPECTED_SOURCE_SHA256,
        capture=True,
    )
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("pressure producer emitted no summary")
    value = json.loads(lines[-1])
    if not isinstance(value, dict):
        raise AssertionError("pressure producer summary was not an object")
    return value


def _wait_completed(harness: ComposeHarness, import_id: str) -> dict[str, Any]:
    return wait_terminal_cleanup(harness, import_id, status="completed", timeout=300)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if len(args.source_sha) != 40:
        raise AssertionError("repository source SHA must be a full commit SHA")
    args.root.mkdir(parents=True, exist_ok=True)
    harness = ComposeHarness(project="mangasensei_e3_pressure", port=18080)
    try:
        harness.start_base()
        harness.start_importer()
        harness.stop_now("pdf-renderer")
        stopped_renderer = harness.stopped_state("pdf-renderer")
        assert stopped_renderer["oomKilled"] is False

        import_id, token, admitted_at = admit(harness, key="e3-pressure-480m-v1")
        harness.wait_request(import_id, 1)
        harness.pause("pdf-importer")
        producer = _run_producer(harness)
        assert producer["profile"] == PROFILE_NAME
        assert producer["importId"] == import_id
        assert producer["fencingToken"] == 1
        assert producer["pages"] == PAGE_COUNT
        assert producer["pageBytes"] == PAGE_BYTES
        assert producer["aggregateBytes"] == AGGREGATE_BYTES
        assert producer["aggregatePixels"] == AGGREGATE_PIXELS
        assert producer["uniqueRasters"] == PAGE_COUNT
        assert len(producer["rasterSha256"]) == PAGE_COUNT
        assert len(set(producer["rasterSha256"])) == PAGE_COUNT

        precommit = harness.db_state(import_id)
        assert precommit["status"] == "rendering"
        assert precommit["fencingToken"] == 1
        assert precommit["documents"] == 0
        assert precommit["pages"] == 0
        assert precommit["jobs"] == 0
        spool_at_manifest = harness.spool_snapshot(import_id)
        assert spool_at_manifest["sourceExists"] is True
        assert spool_at_manifest["sourceBytes"] == 46_282
        assert spool_at_manifest["requestCount"] == 1
        assert spool_at_manifest["outputImportExists"] is True
        assert spool_at_manifest["outputFileCount"] == 61
        assert spool_at_manifest["outputImportBytes"] >= AGGREGATE_BYTES
        storage_before = harness.storage_bytes()
        assert storage_before == 0

        harness.unpause("pdf-importer")
        _wait_completed(harness, import_id)
        total_elapsed = time.perf_counter() - admitted_at
        final = harness.db_state(import_id)
        assert final["status"] == "completed"
        assert final["documents"] == 1
        assert final["pages"] == PAGE_COUNT
        assert final["jobs"] == PAGE_COUNT
        assert final["pendingJobs"] == PAGE_COUNT
        assert final["imageBlobs"] == PAGE_COUNT
        assert final["pageCount"] == PAGE_COUNT
        assert final["fencingToken"] == 1
        assert final["sourceCleaned"] is True
        assert final["errorCode"] is None
        digests = harness.page_digests(import_id)
        assert [item["ordinal"] for item in digests] == list(range(PAGE_COUNT))
        assert [item["sha256"] for item in digests] == producer["rasterSha256"]
        blobs = harness.image_blob_summary(import_id)
        assert blobs == {
            "count": PAGE_COUNT,
            "totalBytes": AGGREGATE_BYTES,
            "ready": PAGE_COUNT,
            "width80Height120": PAGE_COUNT,
            "png": PAGE_COUNT,
        }
        require_import_capability(
            harness,
            import_id=import_id,
            token=token,
            expected_pages=PAGE_COUNT,
        )
        storage_after = harness.storage_bytes()
        assert storage_after >= AGGREGATE_BYTES
        spool_final = harness.spool_snapshot(import_id)
        assert spool_final["sourceExists"] is False
        assert spool_final["requestCount"] == 0
        assert spool_final["outputImportExists"] is False

        importer_resource = harness.resource_snapshot("pdf-importer")
        require_resource(
            importer_resource,
            expected_memory=_PRESSURE_IMPORTER_MEMORY,
            role="pressure-importer",
        )
        timings = probe_timings(
            harness,
            output_dir=args.root,
            import_id=import_id,
            fence=1,
        )
        evidence = {
            "schemaVersion": 1,
            "repositorySourceSha": args.source_sha,
            "profile": PROFILE_NAME,
            "boundary": "renderer-output-substitution",
            "doesNotProveRendererResources": True,
            "workload": source_manifest(generate_pdf()),
            "importId": import_id,
            "fencingToken": 1,
            "generated": {
                "pages": PAGE_COUNT,
                "pageBytes": PAGE_BYTES,
                "aggregateBytes": AGGREGATE_BYTES,
                "aggregatePixels": AGGREGATE_PIXELS,
                "uniqueRasters": PAGE_COUNT,
                "producerElapsedSeconds": producer["elapsedSeconds"],
                "rendererProvenanceFields": producer["rendererProvenance"],
            },
            "precommitDb": precommit,
            "spoolAtManifest": spool_at_manifest,
            "finalDb": final,
            "imageBlobs": blobs,
            "storageBytesBeforeCommit": storage_before,
            "storageBytesAfterCommit": storage_after,
            "terminalSpool": spool_final,
            "importerResource": importer_resource,
            "manifestValidationElapsedSeconds": timings["manifestValidation"],
            "commitElapsedSeconds": timings["commit"],
            "coordinatorProcessElapsedSeconds": timings["overall"],
            "overallImportElapsedSeconds": total_elapsed,
            "headroomReviewThreshold": _HEADROOM_REVIEW,
            "headroomReviewRequired": (
                importer_resource["peakRatio"] is not None
                and importer_resource["peakRatio"] >= _HEADROOM_REVIEW
            ),
            "stoppedProductionRenderer": stopped_renderer,
            "result": "pass",
        }
        evidence_path = args.root / "pressure-evidence.json"
        write_json(evidence_path, evidence)
        result = {
            "schemaVersion": 1,
            "repositorySourceSha": args.source_sha,
            "profile": PROFILE_NAME,
            "result": "pass",
            "evidenceFiles": [
                {
                    "path": evidence_path.name,
                    "sha256": sha256_file(evidence_path),
                    "bytes": evidence_path.stat().st_size,
                }
            ],
        }
        write_json(args.root / "result-manifest.json", result)
        return 0
    finally:
        harness.reset()


if __name__ == "__main__":
    raise SystemExit(main())
