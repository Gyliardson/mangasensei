from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import time
import zlib
from io import BytesIO
from pathlib import Path

from PIL import Image

from mangasensei.config import Settings
from mangasensei.pdf_imports.contracts import (
    PDF_RASTER_CONTRACT_VERSION,
    PdfRasterManifest,
    PdfRasterPage,
    PdfRendererHeartbeat,
    PdfRenderRequest,
)
from mangasensei.pdf_imports.renderer import renderer_provenance
from mangasensei.pdf_imports.spool import PdfSpool

PROFILE_NAME = "PDF_IMPORTER_PROTOCOL_PRESSURE_480M"
PAGE_COUNT = 60
PAGE_BYTES = 8_388_608
AGGREGATE_BYTES = PAGE_COUNT * PAGE_BYTES
WIDTH = 80
HEIGHT = 120
AGGREGATE_PIXELS = PAGE_COUNT * WIDTH * HEIGHT
_PRIVATE_CHUNK = b"msEc"


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def pressure_png(ordinal: int) -> bytes:
    if not 0 <= ordinal < PAGE_COUNT:
        raise ValueError("pressure ordinal out of range")
    color = (ordinal, (ordinal * 73) % 256, (ordinal * 151) % 256)
    row = b"\x00" + bytes(color) * WIDTH
    raw = row * HEIGHT
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0))
    idat = _chunk(b"IDAT", zlib.compress(raw, level=9))
    iend = _chunk(b"IEND", b"")
    private_payload_bytes = PAGE_BYTES - len(signature) - len(ihdr) - len(idat) - len(iend) - 12
    if private_payload_bytes < 4:
        raise AssertionError("pressure PNG private chunk has insufficient payload")
    private_payload = ordinal.to_bytes(4, "big") + bytes(private_payload_bytes - 4)
    result = signature + ihdr + idat + _chunk(_PRIVATE_CHUNK, private_payload) + iend
    if len(result) != PAGE_BYTES:
        raise AssertionError(f"pressure PNG size drift: {len(result)}")
    with Image.open(BytesIO(result)) as image:
        image.load()
        if image.mode != "RGB" or image.size != (WIDTH, HEIGHT):
            raise AssertionError((image.mode, image.size))
    return result


def _heartbeat(spool: PdfSpool, provenance) -> None:
    spool.write_model_atomic(
        spool.heartbeat_path(),
        PdfRendererHeartbeat(
            instance_id=f"e3-pressure-producer-{os.getpid()}",
            monotonic_ns=time.monotonic_ns(),
            renderer=provenance,
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    settings = Settings()
    output_value = os.environ.get("MANGASENSEI_PDF_RENDERER_OUTPUT_ROOT")
    if not output_value:
        raise RuntimeError("pressure producer requires split renderer output channel")
    spool = PdfSpool(settings.pdf_spool_root, Path(output_value))
    provenance = renderer_provenance()
    requests = sorted(spool.requests.glob("*.request.json"))
    if len(requests) != 1:
        raise AssertionError(f"pressure producer expected exactly one render request, got {len(requests)}")
    request = PdfRenderRequest.model_validate(spool.read_json(requests[0]))
    if request.source_sha256 != args.source_sha:
        raise AssertionError("pressure request source identity drift")
    if request.raster_contract != PDF_RASTER_CONTRACT_VERSION:
        raise AssertionError("pressure request raster contract drift")
    spool.prepare_attempt_dir(request.import_id, request.fencing_token)
    _heartbeat(spool, provenance)

    pages: list[PdfRasterPage] = []
    hashes: list[str] = []
    started = time.perf_counter()
    for ordinal in range(PAGE_COUNT):
        content = pressure_png(ordinal)
        digest = hashlib.sha256(content).hexdigest()
        filename = f"page-{ordinal + 1:06d}.png"
        spool.write_bytes_exclusive(
            spool.page_path(request.import_id, request.fencing_token, filename),
            content,
        )
        hashes.append(digest)
        pages.append(
            PdfRasterPage(
                ordinal=ordinal,
                filename=filename,
                sha256=digest,
                byte_size=PAGE_BYTES,
                width=WIDTH,
                height=HEIGHT,
                embedded_rotation=0,
                page_bbox=(0.0, 0.0, 28.7, 43.0),
            )
        )
        _heartbeat(spool, provenance)

    if len(set(hashes)) != PAGE_COUNT:
        raise AssertionError("pressure PNG contents are not unique")
    manifest = PdfRasterManifest(
        import_id=request.import_id,
        fencing_token=request.fencing_token,
        source_sha256=request.source_sha256,
        raster_contract=PDF_RASTER_CONTRACT_VERSION,
        page_count=PAGE_COUNT,
        aggregate_pixels=AGGREGATE_PIXELS,
        aggregate_raster_bytes=AGGREGATE_BYTES,
        pages=tuple(pages),
        renderer=provenance,
    )
    spool.write_model_atomic(
        spool.manifest_path(request.import_id, request.fencing_token),
        manifest,
    )
    _heartbeat(spool, provenance)
    print(
        json.dumps(
            {
                "profile": PROFILE_NAME,
                "importId": str(request.import_id),
                "fencingToken": request.fencing_token,
                "pages": PAGE_COUNT,
                "pageBytes": PAGE_BYTES,
                "aggregateBytes": AGGREGATE_BYTES,
                "aggregatePixels": AGGREGATE_PIXELS,
                "uniqueRasters": len(set(hashes)),
                "elapsedSeconds": time.perf_counter() - started,
                "rasterSha256": hashes,
                "rendererProvenance": provenance.model_dump(mode="json"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
