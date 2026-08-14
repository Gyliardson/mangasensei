"""Deterministic 200-page synthetic workload for Slice E1."""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKLOAD_NAME = "CONTROL_PLANE_MAX_200"
PAGE_COUNT = 200
PAGE_WIDTH = 80
PAGE_HEIGHT = 120
PAGE_PIXELS = PAGE_WIDTH * PAGE_HEIGHT
FROZEN_AGGREGATE_PIXELS = 1_920_000
FROZEN_AGGREGATE_ENCODED_BYTES = 39_780
FROZEN_MIN_ENCODED_BYTES = 108
FROZEN_MAX_ENCODED_BYTES = 201
FROZEN_UNIQUE_CONTENTS = 200
FROZEN_ORDERED_CONTENT_SHA256 = "c60a3b6c1cf2e2219be89286fc917ccc87d89b2b23e84449f4d83e589b60008b"


@dataclass(frozen=True, slots=True)
class GeneratedPage:
    ordinal: int
    filename: str
    rgb: tuple[int, int, int]
    content: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def page_rgb(ordinal: int) -> tuple[int, int, int]:
    if not 0 <= ordinal < PAGE_COUNT:
        raise ValueError(f"ordinal must be in 0..{PAGE_COUNT - 1}")
    return ordinal % 256, (73 * ordinal) % 256, (151 * ordinal) % 256


def page_bytes(ordinal: int) -> bytes:
    """Encode one RGB PNG using the frozen zero-based page ordinal as ``i``."""
    rgb = page_rgb(ordinal)
    scanline = b"\x00" + bytes(rgb) * PAGE_WIDTH
    raw = scanline * PAGE_HEIGHT
    ihdr = struct.pack(">IIBBBBB", PAGE_WIDTH, PAGE_HEIGHT, 8, 2, 0, 0, 0)
    compressed = zlib.compress(raw, level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


def generate_pages() -> tuple[GeneratedPage, ...]:
    return tuple(
        GeneratedPage(
            ordinal=ordinal,
            filename=f"page-{ordinal + 1:06d}.png",
            rgb=page_rgb(ordinal),
            content=page_bytes(ordinal),
        )
        for ordinal in range(PAGE_COUNT)
    )


def workload_manifest(pages: tuple[GeneratedPage, ...] | None = None) -> dict[str, Any]:
    generated = pages or generate_pages()
    sizes = [len(page.content) for page in generated]
    hashes = [page.sha256 for page in generated]
    ordered_content = b"".join(page.content for page in generated)
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "workload": WORKLOAD_NAME,
        "generator": {
            "pageIndex": "zero-based ordinal i=0..199; filename number is i+1",
            "width": PAGE_WIDTH,
            "height": PAGE_HEIGHT,
            "pixelFormat": "RGB",
            "rgbFormula": ["i % 256", "(73*i) % 256", "(151*i) % 256"],
            "pngFilterByte": 0,
            "zlibLevel": 9,
            "chunks": ["IHDR", "IDAT", "IEND"],
            "zlibRuntime": zlib.ZLIB_RUNTIME_VERSION,
        },
        "aggregate": {
            "pageCount": len(generated),
            "pixelCount": len(generated) * PAGE_PIXELS,
            "encodedBytes": sum(sizes),
            "minEncodedBytes": min(sizes),
            "maxEncodedBytes": max(sizes),
            "uniqueImageContents": len(set(hashes)),
            "orderedContentSha256": hashlib.sha256(ordered_content).hexdigest(),
        },
        "pages": [
            {
                "ordinal": page.ordinal,
                "filename": page.filename,
                "rgb": list(page.rgb),
                "encodedBytes": len(page.content),
                "sha256": page.sha256,
            }
            for page in generated
        ],
    }
    validate_frozen_manifest(manifest)
    return manifest


def validate_frozen_manifest(manifest: dict[str, Any]) -> None:
    aggregate = manifest["aggregate"]
    expected = {
        "pageCount": PAGE_COUNT,
        "pixelCount": FROZEN_AGGREGATE_PIXELS,
        "encodedBytes": FROZEN_AGGREGATE_ENCODED_BYTES,
        "minEncodedBytes": FROZEN_MIN_ENCODED_BYTES,
        "maxEncodedBytes": FROZEN_MAX_ENCODED_BYTES,
        "uniqueImageContents": FROZEN_UNIQUE_CONTENTS,
        "orderedContentSha256": FROZEN_ORDERED_CONTENT_SHA256,
    }
    actual = {key: aggregate[key] for key in expected}
    if actual != expected:
        raise RuntimeError(
            "CONTROL_PLANE_MAX_200 generator drifted from the frozen workload: "
            f"expected={expected!r} actual={actual!r}"
        )


def write_workload(output_dir: Path) -> dict[str, Any]:
    pages = generate_pages()
    manifest = workload_manifest(pages)
    output_dir.mkdir(parents=True, exist_ok=True)
    for page in pages:
        (output_dir / page.filename).write_bytes(page.content)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = write_workload(args.output)
    print(json.dumps(manifest["aggregate"], sort_keys=True))


if __name__ == "__main__":
    main()
