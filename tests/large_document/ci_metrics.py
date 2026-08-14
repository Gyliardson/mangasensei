"""Assemble machine-readable Slice E1 runtime evidence without capability values."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    root = args.root
    log = (root / "api.log").read_text(encoding="utf-8", errors="replace")
    responses = [line for line in log.splitlines() if re.search(r'HTTP/1\.[01]" \d{3}', line)]
    unexpected_429s = sum(1 for line in responses if re.search(r'HTTP/1\.[01]" 429 ', line))
    runtime = {
        "schemaVersion": 1,
        "totalHttpResponses": len(responses),
        "unexpected429s": unexpected_429s,
        "rateLimitPerMinute": 120,
        "rateLimitOverride": None,
    }
    (root / "runtime-requests.json").write_text(
        json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if unexpected_429s:
        raise SystemExit("unexpected HTTP 429 observed in dedicated large-document API")

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
                "timing": browser["timing"],
                "requests": browser["requests"],
                "runtimeRequests": runtime,
                "queue": queue["summary"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
