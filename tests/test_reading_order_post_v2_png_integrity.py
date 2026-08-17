from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest
from scripts.reading_order_post_v2_qualification.contracts import ContractError, load_corpus_design
from scripts.reading_order_post_v2_qualification.png_integrity import (
    validate_corpus_image_integrity,
    validate_rgb_png,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
RETIRED_V1_ROOT = REPO_ROOT / "assets" / "reading-order-post-v2" / "heldout-v1"
FORENSIC_INVENTORY = (
    REPO_ROOT
    / "scripts"
    / "reading_order_post_v2_qualification"
    / "forensics"
    / "heldout-v1-png-integrity.json"
)


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _png_bytes(*, width: int = 3, height: int = 2, truncate_idat: bool = False) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes([row + 1]) * (width * 3) for row in range(height))
    compressed = zlib.compress(raw)
    if truncate_idat:
        compressed = compressed[:-2]
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", compressed),
            _chunk(b"IEND", b""),
        )
    )


def _git_blob_sha(payload: bytes) -> str:
    header = f"blob {len(payload)}\0".encode()
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()  # noqa: S324


def test_validate_rgb_png_accepts_complete_noninterlaced_rgb(tmp_path: Path) -> None:
    image = tmp_path / "valid.png"
    image.write_bytes(_png_bytes())

    info = validate_rgb_png(image)

    assert (info.width, info.height) == (3, 2)
    assert (info.bit_depth, info.color_type, info.interlace_method) == (8, 2, 0)


def test_validate_rgb_png_rejects_truncated_idat_even_with_valid_chunk_crc(tmp_path: Path) -> None:
    image = tmp_path / "truncated.png"
    image.write_bytes(_png_bytes(truncate_idat=True))

    with pytest.raises(ContractError, match="invalid or truncated IDAT zlib stream"):
        validate_rgb_png(image)


def test_validate_rgb_png_rejects_chunk_crc_mismatch(tmp_path: Path) -> None:
    image = tmp_path / "crc-mismatch.png"
    payload = bytearray(_png_bytes())
    idat = payload.index(b"IDAT")
    payload[idat + 4] ^= 0x01
    image.write_bytes(payload)

    with pytest.raises(ContractError, match="CRC mismatch in IDAT chunk"):
        validate_rgb_png(image)


def test_retired_v1_png_integrity_inventory_is_exact() -> None:
    design = load_corpus_design(RETIRED_V1_ROOT / "corpus-design.json")
    evidence = json.loads(FORENSIC_INVENTORY.read_text(encoding="utf-8"))
    expected_pages = {page["pageId"]: page for page in evidence["pages"]}
    actual_pages: dict[str, dict[str, object]] = {}

    for page_id in design.page_ids:
        image = RETIRED_V1_ROOT / "images" / f"{page_id}.png"
        payload = image.read_bytes()
        actual: dict[str, object] = {
            "pageId": page_id,
            "file": f"images/{page_id}.png",
            "gitBlobSha": _git_blob_sha(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        try:
            info = validate_rgb_png(image)
        except ContractError as exc:
            prefix = f"{image}: "
            message = str(exc)
            assert message.startswith(prefix)
            actual.update({"valid": False, "error": message.removeprefix(prefix)})
        else:
            actual.update(
                {
                    "valid": True,
                    "mode": "RGB",
                    "width": info.width,
                    "height": info.height,
                }
            )
        actual_pages[page_id] = actual

    assert set(expected_pages) == set(design.page_ids)
    assert actual_pages == expected_pages
    invalid = sorted(page_id for page_id, page in actual_pages.items() if not page["valid"])
    assert invalid == evidence["invalidPageIds"]
    assert len(actual_pages) == evidence["pageCount"] == 24
    assert len(actual_pages) - len(invalid) == evidence["validPageCount"] == 18
    assert evidence["classification"] == "FORENSIC_ONLY_RETIRED_HELDOUT_NOT_SCIENTIFIC_EVIDENCE"


def test_retired_v1_is_rejected_before_any_arm_execution() -> None:
    design = load_corpus_design(RETIRED_V1_ROOT / "corpus-design.json")

    with pytest.raises(ContractError, match=r"Q003\.png: CRC mismatch in IDAT chunk"):
        validate_corpus_image_integrity(RETIRED_V1_ROOT, design.page_ids)
