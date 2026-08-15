from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Final

WORKLOAD_NAME: Final = "PDF_PAGECOUNT_MAX_200"
WORKLOAD_VERSION: Final = "pdf-scale-stdlib-v1"
PAGE_COUNT: Final = 200
MEDIA_BOX: Final = (0.0, 0.0, 28.7, 43.0)
EXPECTED_SOURCE_BYTES: Final = 46_282
EXPECTED_SOURCE_SHA256: Final = "cb181b41e45a46e138b7188d87d54620e4c1738dd654f3e6cb7eadc854ef2cf5"


def generate_pdf() -> bytes:
    payload = bytearray(b"%PDF-1.4\n")
    offsets: list[int | None] = [None] * 403

    def write_object(number: int, body: bytes) -> None:
        offsets[number] = len(payload)
        payload.extend(f"{number} 0 obj\n".encode("ascii"))
        payload.extend(body)
        payload.extend(b"\nendobj\n")

    write_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    page_refs = " ".join(f"{3 + 2 * ordinal} 0 R" for ordinal in range(PAGE_COUNT))
    write_object(2, f"<< /Type /Pages /Count 200 /Kids [{page_refs}] >>".encode("ascii"))

    for ordinal in range(PAGE_COUNT):
        page_object = 3 + 2 * ordinal
        stream_object = 4 + 2 * ordinal
        write_object(
            page_object,
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 28.7 43.0] "
                f"/Resources << >> /Contents {stream_object} 0 R >>"
            ).encode("ascii"),
        )
        x = 1 + (ordinal % 20)
        y = 1 + 4 * (ordinal // 20)
        content = f"q\n0 g\n{x} {y} 1 1 re f\nQ\n".encode("ascii")
        offsets[stream_object] = len(payload)
        payload.extend(f"{stream_object} 0 obj\n".encode("ascii"))
        payload.extend(f"<< /Length {len(content)} >>\n".encode("ascii"))
        payload.extend(b"stream\n")
        payload.extend(content)
        payload.extend(b"endstream\n")
        payload.extend(b"endobj\n")

    xref_offset = len(payload)
    payload.extend(b"xref\n0 403\n")
    payload.extend(b"0000000000 65535 f \n")
    for number in range(1, 403):
        offset = offsets[number]
        if offset is None or offset >= 10_000_000_000:
            raise ValueError(f"invalid xref offset for object {number}: {offset}")
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(b"trailer\n")
    payload.extend(b"<< /Size 403 /Root 1 0 R >>\n")
    payload.extend(b"startxref\n")
    payload.extend(f"{xref_offset}\n".encode("ascii"))
    payload.extend(b"%%EOF\n")

    result = bytes(payload)
    _require_frozen_source(result)
    return result


def source_manifest(content: bytes) -> dict[str, object]:
    _require_frozen_source(content)
    return {
        "schemaVersion": 1,
        "workload": WORKLOAD_NAME,
        "serializer": WORKLOAD_VERSION,
        "sourceBytes": len(content),
        "sourceSha256": hashlib.sha256(content).hexdigest(),
        "pageCount": PAGE_COUNT,
        "mediaBoxPoints": list(MEDIA_BOX),
        "rectangle": {
            "widthPoints": 1,
            "heightPoints": 1,
            "xFormula": "1 + (ordinal % 20)",
            "yFormula": "1 + 4 * (ordinal // 20)",
        },
    }


def _require_frozen_source(content: bytes) -> None:
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != EXPECTED_SOURCE_BYTES or digest != EXPECTED_SOURCE_SHA256:
        raise AssertionError(
            "pdf-scale-stdlib-v1 source drift: "
            f"bytes={len(content)} sha256={digest}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    content = generate_pdf()
    (args.output / "source.pdf").write_bytes(content)
    (args.output / "workload-manifest.json").write_text(
        json.dumps(source_manifest(content), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
