from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.pdf_scale.generator import (
    EXPECTED_SOURCE_BYTES,
    EXPECTED_SOURCE_SHA256,
    PAGE_COUNT,
    WORKLOAD_NAME,
    WORKLOAD_VERSION,
)

_CONTRACT_PATH = Path(__file__).with_name("raster-contract.json")


def load_raster_contract() -> dict[str, Any]:
    value = json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError("frozen raster contract must be a JSON object")
    assert value["schemaVersion"] == 1
    assert value["workloadName"] == WORKLOAD_NAME
    assert value["workloadVersion"] == WORKLOAD_VERSION
    assert value["sourceBytes"] == EXPECTED_SOURCE_BYTES
    assert value["sourceSha256"] == EXPECTED_SOURCE_SHA256
    assert value["pageCount"] == PAGE_COUNT
    assert value["aggregatePixels"] == 1_920_000
    assert value["aggregateRasterBytes"] == 48_223
    assert value["minRasterBytes"] == 236
    assert value["maxRasterBytes"] == 244
    assert value["orderedRasterSha256"] == (
        "275ff16afad710b8d509f5038a57e65ed9952ba5e371a8cbdb84f94b6dfe4bff"
    )
    assert value["calibrationSourceSha"] == (
        "a56e69c055c8b242f90f5d05a780b07af342b340"
    )
    renderer = value["renderer"]
    assert renderer == {
        "native_library": "pypdfium2_raw/libpdfium.so",
        "pdfium": "152.0.7947.0",
        "pdfium_build": 7947,
        "pdfium_flags": [],
        "pillow": "12.3.0",
        "pypdfium2": "5.12.1",
    }
    assert value["width"] == 80
    assert value["height"] == 120
    raw_pages = value["pages"]
    assert isinstance(raw_pages, list)
    assert len(raw_pages) == PAGE_COUNT
    pages = [
        {
            "ordinal": ordinal,
            "filename": f"page-{ordinal + 1:06d}.png",
            "bytes": int(item[0]),
            "sha256": str(item[1]),
            "width": value["width"],
            "height": value["height"],
        }
        for ordinal, item in enumerate(raw_pages)
    ]
    assert len({page["sha256"] for page in pages}) == PAGE_COUNT
    assert min(page["bytes"] for page in pages) == value["minRasterBytes"]
    assert max(page["bytes"] for page in pages) == value["maxRasterBytes"]
    assert sum(page["bytes"] for page in pages) == value["aggregateRasterBytes"]
    value["pages"] = pages
    return value


def require_manifest_matches_frozen(
    manifest: dict[str, Any],
    *,
    import_id: str,
    fencing_token: int,
) -> None:
    frozen = load_raster_contract()
    assert manifest["import_id"] == import_id
    assert manifest["fencing_token"] == fencing_token
    assert manifest["source_sha256"] == EXPECTED_SOURCE_SHA256
    assert manifest["raster_contract"] == frozen["rasterContract"]
    assert manifest["page_count"] == frozen["pageCount"]
    assert manifest["aggregate_pixels"] == frozen["aggregatePixels"]
    assert manifest["aggregate_raster_bytes"] == frozen["aggregateRasterBytes"]
    assert manifest["renderer"] == frozen["renderer"]
    actual_pages = manifest["pages"]
    expected_pages = frozen["pages"]
    assert len(actual_pages) == len(expected_pages)
    for actual, expected in zip(actual_pages, expected_pages, strict=True):
        assert actual["ordinal"] == expected["ordinal"]
        assert actual["filename"] == expected["filename"]
        assert actual["sha256"] == expected["sha256"]
        assert actual["byte_size"] == expected["bytes"]
        assert actual["width"] == expected["width"]
        assert actual["height"] == expected["height"]
        assert actual["embedded_rotation"] == 0
