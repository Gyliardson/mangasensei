from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from tests.pdf_scale.raster_contract import require_manifest_matches_frozen
from tests.pdf_scale.runtime_common import ComposeHarness, wait_until, write_json
from tests.pdf_scale.scale_scenarios import admit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)

    harness = ComposeHarness(project="mangasensei_e3_reclaim_diagnostic", port=18081)
    import_id: str | None = None
    diagnostic: dict[str, Any] = {
        "schemaVersion": 1,
        "scenario": "importer-reclaim-diagnostic",
        "repositorySourceSha": args.source_sha,
    }
    failed = False
    try:
        harness.start_base()
        harness.pause("pdf-renderer")
        harness.start_importer()
        import_id, _token, _started = admit(
            harness,
            key="e3-importer-reclaim-diagnostic-v1",
        )
        diagnostic["importId"] = import_id
        harness.wait_request(import_id, 1)
        harness.pause("pdf-importer")
        harness.unpause("pdf-renderer")
        harness.wait_manifest(import_id, 1)
        require_manifest_matches_frozen(
            harness.read_manifest(import_id, 1),
            import_id=import_id,
            fencing_token=1,
        )
        diagnostic["preCrashDb"] = harness.db_state(import_id)
        diagnostic["preCrashSpool"] = harness.spool_attempt_summary(import_id)

        harness.stop_now("pdf-importer")
        diagnostic["stoppedImporter"] = harness.stopped_state("pdf-importer")
        harness.expire_import_lease(import_id)
        diagnostic["expiredLeaseDb"] = harness.db_state(import_id)

        harness.start("pdf-importer")
        harness.wait_healthy("pdf-importer", 90)

        def higher_fence() -> dict[str, Any] | None:
            state = harness.db_state(import_id)
            return state if state["fencingToken"] >= 2 else None

        reclaimed = wait_until(
            higher_fence,
            timeout=15,
            description="diagnostic higher PDF import fencing token",
            interval=0.1,
        )
        diagnostic["reclaimedDb"] = reclaimed
        harness.wait_request(import_id, 2, timeout=10)
        diagnostic["afterFence2RequestSpool"] = harness.spool_attempt_summary(import_id)
        diagnostic["rendererAfterFence2Request"] = harness.resource_snapshot("pdf-renderer")

        try:
            harness.wait_manifest(import_id, 2, timeout=15)
        except AssertionError as exc:
            failed = True
            diagnostic["fence2ManifestWithin15Seconds"] = False
            diagnostic["failure"] = str(exc)
        else:
            diagnostic["fence2ManifestWithin15Seconds"] = True
            diagnostic["fence2Manifest"] = harness.read_manifest(import_id, 2)

        diagnostic["afterManifestWaitDb"] = harness.db_state(import_id)
        diagnostic["afterManifestWaitSpool"] = harness.spool_attempt_summary(import_id)
        diagnostic["rendererAfterManifestWait"] = harness.resource_snapshot("pdf-renderer")
    except Exception as exc:
        failed = True
        diagnostic["unexpectedFailureType"] = type(exc).__name__
        diagnostic["unexpectedFailure"] = str(exc)
    finally:
        if import_id is not None:
            try:
                diagnostic["finalObservedDb"] = harness.db_state(import_id)
            except Exception as exc:
                diagnostic["finalDbObservationFailure"] = str(exc)
            try:
                diagnostic["finalObservedSpool"] = harness.spool_attempt_summary(import_id)
            except Exception as exc:
                diagnostic["finalSpoolObservationFailure"] = str(exc)
        write_json(args.root / "reclaim-diagnostic.json", diagnostic)
        harness.reset()

    if failed:
        print("importer reclaim diagnostic reproduced a failure", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
