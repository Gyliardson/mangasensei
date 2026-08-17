from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .contracts import ContractError, load_arm_input

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_IHDR = struct.Struct(">IIBBBBB")
_UINT32 = struct.Struct(">I")
_KNOWN_CRITICAL_CHUNKS = frozenset({b"IHDR", b"PLTE", b"IDAT", b"IEND"})


@dataclass(frozen=True, slots=True)
class PngImageInfo:
    width: int
    height: int
    bit_depth: int
    color_type: int
    compression_method: int
    filter_method: int
    interlace_method: int


def _contract_error(path: Path, message: str) -> ContractError:
    return ContractError(f"{path}: {message}")


def validate_rgb_png(path: Path) -> PngImageInfo:
    """Validate the complete byte stream of a non-interlaced 8-bit RGB PNG.

    This intentionally uses only the Python standard library so qualification
    preflight can reject corrupt sealed assets before optional OCR dependencies
    are installed and before any Reading Order arm can execute.
    """

    payload = path.read_bytes()
    if not payload.startswith(PNG_SIGNATURE):
        raise _contract_error(path, "invalid PNG signature")

    offset = len(PNG_SIGNATURE)
    info: PngImageInfo | None = None
    idat_parts: list[bytes] = []
    seen_iend = False

    while offset < len(payload):
        if len(payload) - offset < 12:
            raise _contract_error(path, "truncated PNG chunk header")

        length = _UINT32.unpack_from(payload, offset)[0]
        offset += 4
        chunk_type = payload[offset : offset + 4]
        offset += 4
        if len(chunk_type) != 4:
            raise _contract_error(path, "truncated PNG chunk type")

        remaining = len(payload) - offset
        if length > remaining - 4:
            raise _contract_error(path, f"truncated {chunk_type!r} chunk")

        chunk_data = payload[offset : offset + length]
        offset += length
        stored_crc = _UINT32.unpack_from(payload, offset)[0]
        offset += 4
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(chunk_data, actual_crc) & 0xFFFFFFFF
        if stored_crc != actual_crc:
            chunk_name = chunk_type.decode("ascii", "replace")
            raise _contract_error(path, f"CRC mismatch in {chunk_name} chunk")

        if info is None and chunk_type != b"IHDR":
            raise _contract_error(path, "IHDR must be the first PNG chunk")

        if chunk_type == b"IHDR":
            if info is not None or length != _IHDR.size:
                raise _contract_error(path, "invalid or duplicate IHDR chunk")
            values = _IHDR.unpack(chunk_data)
            info = PngImageInfo(*values)
            if info.width <= 0 or info.height <= 0:
                raise _contract_error(path, "invalid PNG dimensions")
            if (
                info.bit_depth,
                info.color_type,
                info.compression_method,
                info.filter_method,
                info.interlace_method,
            ) != (8, 2, 0, 0, 0):
                raise _contract_error(path, "PNG must be non-interlaced 8-bit RGB")
        elif chunk_type == b"IDAT":
            if info is None or seen_iend:
                raise _contract_error(path, "IDAT appears outside image data sequence")
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0 or seen_iend:
                raise _contract_error(path, "invalid or duplicate IEND chunk")
            seen_iend = True
            if offset != len(payload):
                raise _contract_error(path, "trailing bytes after IEND")
            break
        elif chunk_type[0] & 0x20 == 0 and chunk_type not in _KNOWN_CRITICAL_CHUNKS:
            raise _contract_error(path, f"unknown critical PNG chunk {chunk_type!r}")

    if info is None:
        raise _contract_error(path, "missing IHDR chunk")
    if not idat_parts:
        raise _contract_error(path, "missing IDAT image data")
    if not seen_iend:
        raise _contract_error(path, "missing IEND chunk")

    try:
        decompressed = zlib.decompress(b"".join(idat_parts))
    except zlib.error as exc:
        raise _contract_error(path, f"invalid or truncated IDAT zlib stream: {exc}") from exc

    scanline_size = 1 + info.width * 3
    expected_size = scanline_size * info.height
    if len(decompressed) != expected_size:
        raise _contract_error(
            path,
            f"decoded RGB byte count mismatch: expected {expected_size}, got {len(decompressed)}",
        )
    for row in range(info.height):
        filter_type = decompressed[row * scanline_size]
        if filter_type > 4:
            raise _contract_error(path, f"invalid PNG filter type {filter_type} on row {row}")

    return info


def validate_corpus_image_integrity(corpus_root: Path, page_ids: Iterable[str]) -> None:
    """Fail closed unless every declared page image is fully valid RGB data."""

    for page_id in page_ids:
        page_input = load_arm_input(corpus_root / "inputs" / f"{page_id}.json")
        image_path = corpus_root / "images" / f"{page_id}.png"
        if not image_path.is_file():
            raise _contract_error(image_path, "sealed page image is missing")
        info = validate_rgb_png(image_path)
        if (info.width, info.height) != (page_input.width, page_input.height):
            raise _contract_error(
                image_path,
                "PNG dimensions do not match sealed input dimensions: "
                f"png={info.width}x{info.height}, input={page_input.width}x{page_input.height}",
            )
