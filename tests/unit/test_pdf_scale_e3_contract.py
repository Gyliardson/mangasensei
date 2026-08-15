from __future__ import annotations

from tests.pdf_scale.generator import (
    EXPECTED_SOURCE_BYTES,
    EXPECTED_SOURCE_SHA256,
    WORKLOAD_VERSION,
    generate_pdf,
)
from tests.pdf_scale.raster_contract import load_raster_contract


def test_pdf_e3_frozen_source_and_calibrated_raster_contract_are_bound() -> None:
    content = generate_pdf()
    frozen = load_raster_contract()

    assert len(content) == EXPECTED_SOURCE_BYTES == 46_282
    assert frozen["sourceSha256"] == EXPECTED_SOURCE_SHA256
    assert frozen["workloadVersion"] == WORKLOAD_VERSION == "pdf-scale-stdlib-v1"
    assert frozen["rasterContract"] == "pdfium-raster-v1"
    assert frozen["pageCount"] == 200
    assert frozen["aggregateRasterBytes"] == 48_223
    assert frozen["aggregatePixels"] == 1_920_000
    assert len(frozen["pages"]) == 200
