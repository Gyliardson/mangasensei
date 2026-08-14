from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import UUID

from mangasensei.config import Settings
from mangasensei.pdf_imports.contracts import PdfRasterManifest, PdfRenderRequest
from mangasensei.pdf_imports.spool import PdfSpool

from generator import (
    EXPECTED_SOURCE_SHA256,
    PAGE_COUNT,
    WORKLOAD_NAME,
    WORKLOAD_VERSION,
    generate_pdf,
    source_manifest,
)

CALIBRATION_IMPORT_ID = UUID("00000000-0000-4000-8000-000000000105")


def _spool(settings: Settings) -> PdfSpool:
    output = os.environ.get("MANGASENSEI_PDF_RENDERER_OUTPUT_ROOT")
    if not output:
        raise RuntimeError("split renderer output root is required")
    return PdfSpool(settings.pdf_spool_root, Path(output))


def publish() -> None:
    settings = Settings()
    spool = _spool(settings)
    content = generate_pdf()
    spool.prepare_import_dir(CALIBRATION_IMPORT_ID)
    spool.prepare_attempt_dir(CALIBRATION_IMPORT_ID, 1)
    source = spool.source_path(CALIBRATION_IMPORT_ID)
    spool.write_bytes_exclusive(source, content)
    request = PdfRenderRequest(
        import_id=CALIBRATION_IMPORT_ID,
        fencing_token=1,
        source_sha256=EXPECTED_SOURCE_SHA256,
        max_pages=settings.max_pdf_pages,
        max_side=settings.max_image_side,
        max_page_pixels=settings.max_image_pixels,
        max_aggregate_pixels=settings.max_document_pixels,
        max_page_raster_bytes=settings.max_upload_bytes,
        max_aggregate_raster_bytes=settings.max_pdf_raster_bytes,
        max_spool_bytes=settings.max_pdf_spool_bytes,
    )
    spool.write_model_atomic(spool.request_path(CALIBRATION_IMPORT_ID, 1), request)


def collect(output: Path, *, source_sha: str) -> None:
    settings = Settings()
    spool = _spool(settings)
    manifest_path = spool.manifest_path(CALIBRATION_IMPORT_ID, 1)
    failure_path = spool.failure_path(CALIBRATION_IMPORT_ID, 1)
    deadline = time.monotonic() + settings.pdf_renderer_timeout_seconds + 15
    while time.monotonic() < deadline:
        if manifest_path.exists():
            break
        if failure_path.exists():
            raise AssertionError(f"renderer calibration failed: {spool.read_json(failure_path)}")
        time.sleep(0.1)
    else:
        raise AssertionError("renderer calibration did not produce a manifest")

    manifest = PdfRasterManifest.model_validate(spool.read_json(manifest_path))
    if (
        manifest.import_id != CALIBRATION_IMPORT_ID
        or manifest.fencing_token != 1
        or manifest.source_sha256 != EXPECTED_SOURCE_SHA256
        or manifest.page_count != PAGE_COUNT
    ):
        raise AssertionError("calibration manifest identity drift")

    ordered = hashlib.sha256()
    pages: list[dict[str, object]] = []
    sizes: list[int] = []
    for page in manifest.pages:
        path = spool.page_path(CALIBRATION_IMPORT_ID, 1, page.filename)
        metadata = spool.require_regular_file(path, max_bytes=settings.max_upload_bytes)
        if metadata.st_size != page.byte_size:
            raise AssertionError("calibration raster size mismatch")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != page.sha256:
            raise AssertionError("calibration raster digest mismatch")
        if (page.width, page.height) != (80, 120):
            raise AssertionError(f"unexpected calibration geometry: {(page.width, page.height)}")
        ordered.update(content)
        sizes.append(page.byte_size)
        pages.append(
            {
                "ordinal": page.ordinal,
                "filename": page.filename,
                "bytes": page.byte_size,
                "sha256": page.sha256,
                "width": page.width,
                "height": page.height,
            }
        )

    if manifest.aggregate_pixels != 1_920_000:
        raise AssertionError(f"unexpected aggregate pixels: {manifest.aggregate_pixels}")
    if manifest.aggregate_raster_bytes != sum(sizes):
        raise AssertionError("aggregate raster byte mismatch")

    evidence = {
        "schemaVersion": 1,
        "mode": "calibration",
        "repositorySourceSha": source_sha,
        "workload": source_manifest(generate_pdf()),
        "workloadName": WORKLOAD_NAME,
        "workloadVersion": WORKLOAD_VERSION,
        "rasterContract": manifest.raster_contract,
        "renderer": manifest.renderer.model_dump(mode="json"),
        "pageCount": manifest.page_count,
        "aggregateRasterBytes": manifest.aggregate_raster_bytes,
        "minRasterBytes": min(sizes),
        "maxRasterBytes": max(sizes),
        "aggregatePixels": manifest.aggregate_pixels,
        "orderedRasterSha256": ordered.hexdigest(),
        "pages": pages,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("publish")
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--output", type=Path, required=True)
    collect_parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if args.command == "publish":
        publish()
    else:
        collect(args.output, source_sha=args.source_sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
