"""Assemble machine-readable Slice E1 runtime evidence without capability values."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _runtime_requests(root: Path) -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in (root / "runtime-http.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    unexpected_429s = sum(1 for record in records if record["status"] == 429)
    methods = Counter(str(record["method"]) for record in records)
    statuses = Counter(str(record["status"]) for record in records)
    runtime = {
        "schemaVersion": 1,
        "totalHttpResponses": len(records),
        "unexpected429s": unexpected_429s,
        "rateLimitPerMinute": 120,
        "rateLimitOverride": None,
        "methods": dict(sorted(methods.items())),
        "statuses": dict(sorted(statuses.items())),
    }
    (root / "runtime-requests.json").write_text(
        json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if unexpected_429s:
        raise SystemExit("unexpected HTTP 429 observed in dedicated large-document API")
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    root = args.root
    runtime = _runtime_requests(root)

    workload = _load(root / "input" / "manifest.json")
    initial = _load(root / "db-initial.json")
    final = _load(root / "db-final.json")
    browser = _load(root / "browser.json")
    queue = _load(root / "queue-claims.json")
    result = {
        "schemaVersion": 1,
        "sourceSha": args.source_sha,
        "workload": {
            "name": workload["workload"],
            "aggregate": workload["aggregate"],
            "sampledPageSha256": {
                "page-000001.png": workload["pages"][0]["sha256"],
                "page-000100.png": workload["pages"][99]["sha256"],
                "page-000200.png": workload["pages"][199]["sha256"],
            },
        },
        "worker": {
            "pipeline": "mangasensei.workers.runner.Worker",
            "externalOcrBoundary": "DeterministicLargeDocumentOcr",
            "gemini": "disabled",
            "networkRequests": 0,
            "intentionalSleepPerPage": False,
        },
        "initial": initial,
        "final": final,
        "browser": browser,
        "runtimeRequests": runtime,
        "queueCharacterization": queue["summary"],
        "rateLimitContract": {
            "productionDefaultPerMinute": 120,
            "dedicatedOverride": None,
            "browserModelMaxApiRequests": 67,
            "aggregateGetMax": 60,
        },
        "scope": {
            "queueFairness": "characterization-only",
            "retentionHardening": "deferred-to-E2",
            "pdfResourceRestartWork": "deferred-to-E3",
            "fairnessPolicy": "deferred-to-E4",
            "composeAcceptance": "deferred-to-E5",
        },
    }
    (root / "result-manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "workload": result["workload"]["aggregate"],
                "initialDb": initial["db"],
                "finalDb": final["db"],
                "aggregateProjectionInitial": initial["aggregateProjection"],
                "aggregateProjectionFinal": final["aggregateProjection"],
                "timing": browser["timing"],
                "requests": browser["requests"],
                "blobLifecycle": browser["blobLifecycle"],
                "browser": browser["browser"],
                "runtimeRequests": runtime,
                "queue": queue["summary"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
