"""Assemble machine-readable Slice E1 runtime evidence without capability values."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

_DOCUMENT_AGGREGATE = re.compile(r"^/api/v1/documents/[^/]+$")
_DOCUMENT_PAGE = re.compile(r"^/api/v1/documents/[^/]+/pages/[^/]+$")
_DOCUMENT_IMAGE = re.compile(r"^/api/v1/documents/[^/]+/pages/[^/]+/image$")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _runtime_requests(root: Path) -> dict[str, Any]:
    records = [
        json.loads(line)
        for line in (root / "runtime-http.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    api_records = [record for record in records if str(record["path"]).startswith("/api/")]
    upload_posts = [
        record
        for record in api_records
        if record["method"] == "POST" and record["path"] == "/api/v1/documents"
    ]
    aggregate_gets = [
        record
        for record in api_records
        if record["method"] == "GET" and _DOCUMENT_AGGREGATE.fullmatch(str(record["path"]))
    ]
    study_page_gets = [
        record
        for record in api_records
        if record["method"] == "GET" and _DOCUMENT_PAGE.fullmatch(str(record["path"]))
    ]
    image_gets = [
        record
        for record in api_records
        if record["method"] == "GET" and _DOCUMENT_IMAGE.fullmatch(str(record["path"]))
    ]
    classified_api_count = (
        len(upload_posts) + len(aggregate_gets) + len(study_page_gets) + len(image_gets)
    )
    document_scoped = aggregate_gets + study_page_gets + image_gets
    unexpected_429s = sum(1 for record in records if record["status"] == 429)
    methods = Counter(str(record["method"]) for record in records)
    statuses = Counter(str(record["status"]) for record in records)
    runtime = {
        "schemaVersion": 1,
        "totalHttpResponses": len(records),
        "apiResponses": len(api_records),
        "uploadPosts": len(upload_posts),
        "aggregateGets": len(aggregate_gets),
        "studyPageGets": len(study_page_gets),
        "imageGets": len(image_gets),
        "allApiRequestsClassified": classified_api_count == len(api_records),
        "allDocumentScopedRequestsHaveTokenHeader": all(
            bool(record.get("documentTokenHeaderPresent")) for record in document_scoped
        ),
        "unexpected429s": unexpected_429s,
        "rateLimitPerMinute": 120,
        "rateLimitOverride": None,
        "methods": dict(sorted(methods.items())),
        "statuses": dict(sorted(statuses.items())),
        "nonSuccessfulApiRequests": [
            {
                "method": record["method"],
                "path": record["path"],
                "status": record["status"],
                "documentTokenHeaderPresent": record.get("documentTokenHeaderPresent", False),
                "secFetchDest": record.get("secFetchDest", ""),
                "secFetchMode": record.get("secFetchMode", ""),
                "secFetchSite": record.get("secFetchSite", ""),
            }
            for record in api_records
            if not 200 <= int(record["status"]) < 300
        ],
    }
    (root / "runtime-requests.json").write_text(
        json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    failures: list[str] = []
    if len(upload_posts) != 1:
        failures.append(f"expected exactly one document upload POST, observed {len(upload_posts)}")
    if len(aggregate_gets) > 60:
        failures.append(f"aggregate GET count exceeded 60: {len(aggregate_gets)}")
    if len(study_page_gets) != 3:
        failures.append(f"expected three sampled StudyPage GETs, observed {len(study_page_gets)}")
    if len(image_gets) != 3:
        failures.append(f"expected three sampled image GETs, observed {len(image_gets)}")
    if classified_api_count != len(api_records):
        failures.append(
            f"unclassified API requests observed: {len(api_records) - classified_api_count}"
        )
    if len(api_records) > 67:
        failures.append(f"browser API envelope exceeded 67 requests: {len(api_records)}")
    if not runtime["allDocumentScopedRequestsHaveTokenHeader"]:
        failures.append("document-scoped API request observed without X-Document-Token")
    if runtime["nonSuccessfulApiRequests"]:
        failures.append(
            "non-successful API requests observed: "
            + json.dumps(runtime["nonSuccessfulApiRequests"], sort_keys=True)
        )
    if unexpected_429s:
        failures.append("unexpected HTTP 429 observed in dedicated large-document API")
    if failures:
        raise SystemExit("; ".join(failures))
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
