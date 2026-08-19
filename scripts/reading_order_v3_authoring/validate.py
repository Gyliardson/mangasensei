from __future__ import annotations

import os
import stat
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from .canonical import canonical_json_bytes, file_record, sha256_path
from .contracts import (
    MANIFEST_SCHEMA_VERSION,
    ContractError,
    CorpusDesign,
    CoverageSummary,
    PageAnnotation,
    load_annotation,
    load_design,
    load_input,
    validate_authoring_coverage,
)

DESIGN_FILE = "corpus-design.json"
MANIFEST_FILE = "manifest.json"
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


def _secure_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise ContractError(f"{root}: corpus root must be a real directory, not a symlink")
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"{root}: cannot resolve corpus root") from exc


def _require_regular_file(root: Path, relative: str) -> Path:
    root_resolved = _secure_root(root)
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ContractError(f"{relative}: symlinked sealed path is forbidden")
        if not current.exists():
            raise ContractError(f"{relative}: regular file required")
    try:
        info = current.lstat()
    except OSError as exc:
        raise ContractError(f"{relative}: regular file required") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ContractError(f"{relative}: regular file required")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"{relative}: cannot resolve sealed file") from exc
    if not resolved.is_relative_to(root_resolved):
        raise ContractError(f"{relative}: sealed path escapes corpus root")
    return current


def _actual_files(root: Path) -> set[str]:
    _secure_root(root)
    result: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        parent = Path(dirpath)
        for dirname in tuple(dirnames):
            path = parent / dirname
            if path.is_symlink():
                relative = path.relative_to(root).as_posix()
                raise ContractError(f"{relative}: symlinked sealed directory is forbidden")
        for filename in filenames:
            path = parent / filename
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                raise ContractError(f"{relative}: symlinked sealed file is forbidden")
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise ContractError(f"{relative}: cannot inspect sealed file") from exc
            if not stat.S_ISREG(mode):
                raise ContractError(f"{relative}: sealed inventory entries must be regular files")
            if relative != MANIFEST_FILE:
                result.add(relative)
    return result


def _required_paths(design: CorpusDesign) -> set[str]:
    paths = {DESIGN_FILE}
    for page in design.pages:
        paths.update((page.source, page.image, page.input, page.annotation))
    return paths


def _validate_exact_files(root: Path, design: CorpusDesign) -> None:
    required = _required_paths(design)
    for relative in required:
        _require_regular_file(root, relative)
    actual = _actual_files(root)
    if actual != required:
        raise ContractError(
            "corpus file inventory mismatch: "
            f"missing={sorted(required-actual)}, extra={sorted(actual-required)}"
        )


def _png_error(path: Path, message: str) -> ContractError:
    return ContractError(f"{path}: {message}")


def validate_rgb_png(path: Path) -> PngImageInfo:
    payload = path.read_bytes()
    if not payload.startswith(PNG_SIGNATURE):
        raise _png_error(path, "invalid PNG signature")

    offset = len(PNG_SIGNATURE)
    info: PngImageInfo | None = None
    idat_parts: list[bytes] = []
    seen_iend = False

    while offset < len(payload):
        if len(payload) - offset < 12:
            raise _png_error(path, "truncated PNG chunk header")
        length = _UINT32.unpack_from(payload, offset)[0]
        offset += 4
        chunk_type = payload[offset : offset + 4]
        offset += 4
        if len(chunk_type) != 4:
            raise _png_error(path, "truncated PNG chunk type")
        remaining = len(payload) - offset
        if length > remaining - 4:
            raise _png_error(path, f"truncated {chunk_type!r} chunk")
        chunk_data = payload[offset : offset + length]
        offset += length
        stored_crc = _UINT32.unpack_from(payload, offset)[0]
        offset += 4
        actual_crc = zlib.crc32(chunk_type)
        actual_crc = zlib.crc32(chunk_data, actual_crc) & 0xFFFFFFFF
        if stored_crc != actual_crc:
            chunk_name = chunk_type.decode("ascii", "replace")
            raise _png_error(path, f"CRC mismatch in {chunk_name} chunk")

        if info is None and chunk_type != b"IHDR":
            raise _png_error(path, "IHDR must be the first PNG chunk")
        if chunk_type == b"IHDR":
            if info is not None or length != _IHDR.size:
                raise _png_error(path, "invalid or duplicate IHDR chunk")
            info = PngImageInfo(*_IHDR.unpack(chunk_data))
            if info.width <= 0 or info.height <= 0:
                raise _png_error(path, "invalid PNG dimensions")
            if (
                info.bit_depth,
                info.color_type,
                info.compression_method,
                info.filter_method,
                info.interlace_method,
            ) != (8, 2, 0, 0, 0):
                raise _png_error(path, "PNG must be non-interlaced 8-bit RGB")
        elif chunk_type == b"IDAT":
            if info is None or seen_iend:
                raise _png_error(path, "IDAT appears outside image data sequence")
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0 or seen_iend:
                raise _png_error(path, "invalid or duplicate IEND chunk")
            seen_iend = True
            if offset != len(payload):
                raise _png_error(path, "trailing bytes after IEND")
            break
        elif chunk_type[0] & 0x20 == 0 and chunk_type not in _KNOWN_CRITICAL_CHUNKS:
            raise _png_error(path, f"unknown critical PNG chunk {chunk_type!r}")

    if info is None:
        raise _png_error(path, "missing IHDR chunk")
    if not idat_parts:
        raise _png_error(path, "missing IDAT image data")
    if not seen_iend:
        raise _png_error(path, "missing IEND chunk")

    scanline_size = 1 + info.width * 3
    expected_size = scanline_size * info.height
    decompressor = zlib.decompressobj()
    try:
        decompressed = decompressor.decompress(b"".join(idat_parts), expected_size + 1)
    except zlib.error as exc:
        raise _png_error(path, f"invalid or truncated IDAT zlib stream: {exc}") from exc
    if not decompressor.eof:
        raise _png_error(path, "invalid or truncated IDAT zlib stream: end marker not reached")
    if decompressor.unused_data or decompressor.unconsumed_tail:
        raise _png_error(path, "unexpected bytes after IDAT zlib stream")
    if len(decompressed) != expected_size:
        raise _png_error(
            path,
            f"decoded RGB byte count mismatch: expected {expected_size}, got {len(decompressed)}",
        )
    for row in range(info.height):
        filter_type = decompressed[row * scanline_size]
        if filter_type > 4:
            raise _png_error(path, f"invalid PNG filter type {filter_type} on row {row}")
    return info


def _validate_page_contents(
    root: Path,
    design: CorpusDesign,
) -> dict[str, PageAnnotation]:
    annotations: dict[str, PageAnnotation] = {}
    for page in design.pages:
        _require_regular_file(root, page.source)
        image_path = _require_regular_file(root, page.image)
        input_path = _require_regular_file(root, page.input)
        annotation_path = _require_regular_file(root, page.annotation)
        page_input = load_input(input_path)
        annotation = load_annotation(annotation_path)
        if page_input.page_id != page.page_id or annotation.page_id != page.page_id:
            raise ContractError(f"{page.page_id}: pageId mismatch across design/input/annotation")
        input_ids = {region.region_id for region in page_input.regions}
        annotation_ids = set(annotation.reading_order) | set(annotation.unscored_region_ids)
        if input_ids != annotation_ids:
            raise ContractError(f"{page.page_id}: input/annotation region inventory mismatch")
        info = validate_rgb_png(image_path)
        if (info.width, info.height) != (page_input.width, page_input.height):
            raise ContractError(f"{page.page_id}: PNG/input dimension mismatch")
        annotations[page.page_id] = annotation
    return annotations


def _manifest_data(root: Path, design: CorpusDesign) -> dict[str, object]:
    pages: list[dict[str, object]] = []
    for page in design.pages:
        pages.append(
            {
                "pageId": page.page_id,
                "source": {"file": page.source, "sha256": sha256_path(root / page.source)},
                "image": {"file": page.image, "sha256": sha256_path(root / page.image)},
                "input": {"file": page.input, "sha256": sha256_path(root / page.input)},
                "annotation": {
                    "file": page.annotation,
                    "sha256": sha256_path(root / page.annotation),
                },
            }
        )
    inventory = [file_record(root, relative) for relative in sorted(_required_paths(design))]
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "corpusId": design.corpus_id,
        "version": design.version,
        "design": {"file": DESIGN_FILE, "sha256": sha256_path(root / DESIGN_FILE)},
        "pages": pages,
        "inventory": inventory,
    }


def build_manifest(root: Path) -> dict[str, object]:
    _secure_root(root)
    design_path = _require_regular_file(root, DESIGN_FILE)
    design = load_design(design_path)
    _validate_exact_files(root, design)
    annotations = _validate_page_contents(root, design)
    validate_authoring_coverage(design, annotations)
    return _manifest_data(root, design)


def _write_manifest_bytes(root: Path, payload: bytes) -> Path:
    _secure_root(root)
    target = root / MANIFEST_FILE
    if target.is_symlink():
        raise ContractError("manifest.json: symlinked sealed path is forbidden")
    if target.exists() and not stat.S_ISREG(target.lstat().st_mode):
        raise ContractError("manifest.json: regular file required")
    fd, temporary_name = tempfile.mkstemp(prefix=".manifest.", suffix=".tmp", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def write_manifest(root: Path) -> Path:
    manifest = build_manifest(root)
    return _write_manifest_bytes(root, canonical_json_bytes(manifest))


def validate_corpus(root: Path) -> CoverageSummary:
    _secure_root(root)
    design_path = _require_regular_file(root, DESIGN_FILE)
    manifest_path = _require_regular_file(root, MANIFEST_FILE)
    design = load_design(design_path)
    _validate_exact_files(root, design)
    annotations = _validate_page_contents(root, design)
    coverage = validate_authoring_coverage(design, annotations)
    expected_manifest = _manifest_data(root, design)
    expected_bytes = canonical_json_bytes(expected_manifest)
    if manifest_path.read_bytes() != expected_bytes:
        raise ContractError("manifest: canonical bytes or recomputed sealed content mismatch")
    return coverage
