from __future__ import annotations

import ast
import copy
import json
import os
import struct
import zlib
from pathlib import Path

import pytest
from scripts.reading_order_v3_authoring import (
    ANNOTATION_SCHEMA_VERSION,
    AUTHORING_SLICES,
    AUTHORSHIP_BOUNDARY,
    C3_REJECTION_FAMILY,
    DESIGN_MINIMA,
    DESIGN_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    POSITIVE_FAMILIES,
    SLICE_MINIMA,
    ContractError,
    build_manifest,
    validate_corpus,
    validate_rgb_png,
    write_canonical_json,
    write_manifest,
)
from scripts.reading_order_v3_authoring.canonical import canonical_json_bytes
from scripts.reading_order_v3_authoring.contracts import load_annotation, load_design, load_input

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "scripts" / "reading_order_v3_authoring"
AUTHOR_SAFE_FILES = frozenset(
    {
        "README.md",
        "__init__.py",
        "__main__.py",
        "canonical.py",
        "contracts.py",
        "validate.py",
    }
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _png_bytes(width: int = 7, height: int = 5, *, interlace: int = 0) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, interlace)
    rows = b"".join(b"\x00" + bytes([row % 251]) * (width * 3) for row in range(height))
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(rows))
        + _chunk(b"IEND", b"")
    )


def _page_record(page_id: str, index: int) -> dict[str, object]:
    positive = POSITIVE_FAMILIES[index] if index < len(POSITIVE_FAMILIES) else None
    return {
        "pageId": page_id,
        "source": f"source/{page_id}.bin",
        "image": f"images/{page_id}.png",
        "input": f"inputs/{page_id}.json",
        "annotation": f"annotations/{page_id}.json",
        "authoringCoverage": {
            "positiveFamilies": [] if positive is None else [positive],
            "primaryPositiveFamily": positive,
            "c3Rejection": index >= 16,
        },
    }


def _slices_for_page(index: int) -> set[str]:
    slices = {"clean-control"}
    if 0 <= index <= 3:
        slices.update({"c1-boundary-positive", "b1-horizontal"})
    if 4 <= index <= 7:
        slices.update({"c1-near-boundary-negative", "b1-vertical"})
    if 8 <= index <= 10:
        slices.update(
            {
                "c2-gutter-bridge",
                "c2-ambiguous-overlap-bridge",
                "c2-pair-precedence-slot",
            }
        )
    if 11 <= index <= 13:
        slices.add("c2-one-sided-non-unique-fail-closed")
    if 14 <= index <= 15:
        slices.add("c2-conflict-cycle-safety")
    if 16 <= index <= 19:
        slices.add("c3-positive-recovery")
    if 12 <= index <= 15:
        slices.add("b1-mixed-orientation")
    if 8 <= index <= 11:
        slices.add("combined-c1-c2-c3-b1")
    if 18 <= index <= 20:
        slices.add("intentional-fallback")
    return slices


def _design(
    *,
    corpus_id: str = "fresh-neutral-corpus",
    version: str = "draft-v3.1",
    page_ids: tuple[str, ...] | None = None,
) -> dict[str, object]:
    ids = page_ids or tuple(f"page-{index:02d}" for index in range(24))
    return {
        "schemaVersion": DESIGN_SCHEMA_VERSION,
        "corpusId": corpus_id,
        "version": version,
        "authorshipBoundary": AUTHORSHIP_BOUNDARY,
        "provenanceDeclaration": {
            "priorHeldoutEvidenceInspected": False,
            "calibrationOutputsInspected": False,
            "candidateDiagnosticsInspected": False,
            "candidateExecuted": False,
            "qualificationExecuted": False,
            "annotationsAdaptedToCandidateOutput": False,
        },
        "pages": [_page_record(page_id, index) for index, page_id in enumerate(ids)],
    }


def _page_payloads(page_id: str, index: int) -> tuple[dict[str, object], dict[str, object]]:
    region_ids = [f"region {index}:{position}" for position in range(4)]
    regions = [
        {
            "regionId": region_id,
            "sourceIndex": source_index,
            "lines": [
                [
                    [source_index, 0],
                    [source_index + 1, 0],
                    [source_index + 1, 1],
                    [source_index, 1],
                ]
            ],
            "angle": float(source_index),
        }
        for source_index, region_id in enumerate(region_ids)
    ]
    endpoints = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3))
    slices = sorted(_slices_for_page(index))
    pairs = [
        {
            "id": f"pair-{index:02d}-{pair_index}",
            "earlier": region_ids[earlier],
            "later": region_ids[later],
            "slices": slices,
        }
        for pair_index, (earlier, later) in enumerate(endpoints)
    ]
    input_payload = {
        "schemaVersion": INPUT_SCHEMA_VERSION,
        "pageId": page_id,
        "width": 7,
        "height": 5,
        "regions": regions,
    }
    annotation_payload = {
        "schemaVersion": ANNOTATION_SCHEMA_VERSION,
        "pageId": page_id,
        "readingOrder": region_ids,
        "unscoredRegionIds": [],
        "qualificationPairs": pairs,
    }
    return input_payload, annotation_payload


def _write_unsealed_corpus(
    root: Path, design: dict[str, object] | None = None
) -> dict[str, object]:
    payload = copy.deepcopy(design or _design())
    write_canonical_json(root / "corpus-design.json", payload)
    pages = payload["pages"]
    assert isinstance(pages, list)
    for index, page in enumerate(pages):
        assert isinstance(page, dict)
        page_id = str(page["pageId"])
        source_path = root / str(page["source"])
        image_path = root / str(page["image"])
        input_path = root / str(page["input"])
        annotation_path = root / str(page["annotation"])
        source_path.parent.mkdir(parents=True, exist_ok=True)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.parent.mkdir(parents=True, exist_ok=True)
        annotation_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(f"opaque provenance bytes for {page_id}\n".encode())
        image_path.write_bytes(_png_bytes())
        input_payload, annotation_payload = _page_payloads(page_id, index)
        write_canonical_json(input_path, input_payload)
        write_canonical_json(annotation_path, annotation_payload)
    return payload


def _write_corpus(root: Path, design: dict[str, object] | None = None) -> dict[str, object]:
    payload = _write_unsealed_corpus(root, design)
    write_manifest(root)
    return payload


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _annotation_path(root: Path, page_index: int) -> Path:
    return root / "annotations" / f"page-{page_index:02d}.json"


def _remove_slice(root: Path, slice_name: str, page_indexes: set[int] | None = None) -> None:
    for page_index in range(24):
        if page_indexes is not None and page_index not in page_indexes:
            continue
        path = _annotation_path(root, page_index)
        payload = _read_json(path)
        pairs = payload["qualificationPairs"]
        assert isinstance(pairs, list)
        for pair in pairs:
            assert isinstance(pair, dict)
            raw_slices = pair["slices"]
            assert isinstance(raw_slices, list)
            pair["slices"] = [item for item in raw_slices if item != slice_name]
        write_canonical_json(path, payload)


def test_valid_synthetic_fixture_satisfies_all_declassified_minima(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    summary = validate_corpus(tmp_path)
    assert summary.total_qualification_pairs == 120
    assert summary.total_scored_regions == 96
    assert len(summary.c3_rejection_pages) == 8
    assert set(summary.dedicated_positive_pages) == set(POSITIVE_FAMILIES)
    for slice_name, minima in SLICE_MINIMA.items():
        assert summary.slice_pair_counts[slice_name] >= minima["minPairs"]
        assert summary.slice_page_counts[slice_name] >= minima["minPages"]
    assert len(summary.design_role_pages["combined-mechanism"]) >= 4
    assert len(summary.design_role_pages["intentional-fallback"]) >= 3
    assert len(summary.design_role_pages["clean-control"]) >= 6


def test_arbitrary_new_identity_version_and_page_ids_are_accepted(tmp_path: Path) -> None:
    page_ids = tuple(f"chapter.alpha-{index}" for index in range(24))
    _write_corpus(
        tmp_path,
        _design(corpus_id="new-corpus.identity", version="future+draft", page_ids=page_ids),
    )
    summary = validate_corpus(tmp_path)
    assert len(summary.c3_rejection_pages) == 8


def test_no_historical_image_dimension_or_page_id_pattern_is_required(tmp_path: Path) -> None:
    page_ids = tuple(f"id-x-{index}" for index in range(24))
    _write_corpus(tmp_path, _design(page_ids=page_ids))
    assert validate_corpus(tmp_path).total_scored_regions == 96


def test_region_id_is_only_nonempty_unique_string(tmp_path: Path) -> None:
    _write_unsealed_corpus(tmp_path)
    path = tmp_path / "inputs" / "page-00.json"
    payload = _read_json(path)
    regions = payload["regions"]
    assert isinstance(regions, list)
    assert isinstance(regions[0], dict)
    regions[0]["regionId"] = "自由 region / punctuation:#"
    write_canonical_json(path, payload)
    annotation_path = _annotation_path(tmp_path, 0)
    annotation = _read_json(annotation_path)
    reading = annotation["readingOrder"]
    pairs = annotation["qualificationPairs"]
    assert isinstance(reading, list) and isinstance(pairs, list)
    old_id = reading[0]
    reading[0] = "自由 region / punctuation:#"
    for pair in pairs:
        assert isinstance(pair, dict)
        if pair["earlier"] == old_id:
            pair["earlier"] = reading[0]
        if pair["later"] == old_id:
            pair["later"] = reading[0]
    write_canonical_json(annotation_path, annotation)
    write_manifest(tmp_path)
    assert validate_corpus(tmp_path).total_scored_regions == 96


def test_fewer_than_24_pages_is_rejected(tmp_path: Path) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list)
    design["pages"] = pages[:23]
    write_canonical_json(tmp_path / "corpus-design.json", design)
    with pytest.raises(ContractError, match="at least 24 pages"):
        load_design(tmp_path / "corpus-design.json")


def test_fewer_than_120_qualification_pairs_is_rejected(tmp_path: Path) -> None:
    _write_unsealed_corpus(tmp_path)
    path = _annotation_path(tmp_path, 23)
    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list)
    pairs.pop()
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="qualification pairs below frozen minimum"):
        build_manifest(tmp_path)


def test_fewer_than_96_scored_regions_is_rejected(tmp_path: Path) -> None:
    _write_unsealed_corpus(tmp_path)
    path = _annotation_path(tmp_path, 23)
    payload = _read_json(path)
    reading = payload["readingOrder"]
    pairs = payload["qualificationPairs"]
    assert isinstance(reading, list) and isinstance(pairs, list)
    removed = reading.pop()
    payload["unscoredRegionIds"] = [removed]
    payload["qualificationPairs"] = [
        pair
        for pair in pairs
        if isinstance(pair, dict) and removed not in (pair["earlier"], pair["later"])
    ]
    write_canonical_json(path, payload)
    for other_index in (21, 22):
        other_path = _annotation_path(tmp_path, other_index)
        other = _read_json(other_path)
        other_pairs = other["qualificationPairs"]
        other_reading = other["readingOrder"]
        assert isinstance(other_pairs, list) and isinstance(other_reading, list)
        template = copy.deepcopy(other_pairs[0])
        assert isinstance(template, dict)
        template["id"] = f"extra-{other_index}"
        template["earlier"] = other_reading[2]
        template["later"] = other_reading[3]
        other_pairs.append(template)
        write_canonical_json(other_path, other)
    with pytest.raises(ContractError, match="scored regions below frozen minimum"):
        build_manifest(tmp_path)


@pytest.mark.parametrize(
    ("slice_name", "expected"),
    [
        ("combined-c1-c2-c3-b1", "combined-mechanism pages below frozen design minimum"),
        ("intentional-fallback", "intentional-fallback pages below frozen design minimum"),
        ("clean-control", "clean-control pages below frozen design minimum"),
    ],
)
def test_design_role_page_minima_are_enforced(
    tmp_path: Path, slice_name: str, expected: str
) -> None:
    _write_unsealed_corpus(tmp_path)
    if slice_name == "clean-control":
        _remove_slice(tmp_path, slice_name, set(range(19)))
    else:
        _remove_slice(tmp_path, slice_name)
    with pytest.raises(ContractError, match=expected):
        build_manifest(tmp_path)


@pytest.mark.parametrize(
    "slice_name",
    [
        name
        for name in AUTHORING_SLICES
        if name not in {
            "combined-c1-c2-c3-b1",
            "clean-control",
            "intentional-fallback",
        }
    ],
)
def test_each_non_role_slice_minimum_is_enforced(tmp_path: Path, slice_name: str) -> None:
    _write_unsealed_corpus(tmp_path)
    _remove_slice(tmp_path, slice_name)
    with pytest.raises(ContractError, match=rf"slice {slice_name} below frozen minima"):
        build_manifest(tmp_path)


def test_generic_c3_rejection_requires_eight_unique_pages_and_counts_page_once(
    tmp_path: Path,
) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list)
    for index, page in enumerate(pages):
        assert isinstance(page, dict)
        coverage = page["authoringCoverage"]
        assert isinstance(coverage, dict)
        coverage["c3Rejection"] = 16 <= index <= 22
    _write_unsealed_corpus(tmp_path, design)
    with pytest.raises(ContractError, match=rf"{C3_REJECTION_FAMILY} requires at least 8"):
        build_manifest(tmp_path)

    design = _design()
    _write_corpus(tmp_path, design)
    summary = validate_corpus(tmp_path)
    assert len(summary.c3_rejection_pages) == 8
    assert len(set(summary.c3_rejection_pages)) == 8


def test_all_eight_dedicated_positive_families_are_required(tmp_path: Path) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list) and isinstance(pages[0], dict)
    coverage = pages[0]["authoringCoverage"]
    assert isinstance(coverage, dict)
    coverage["positiveFamilies"] = []
    coverage["primaryPositiveFamily"] = None
    _write_unsealed_corpus(tmp_path, design)
    with pytest.raises(ContractError, match="missing dedicated positive families"):
        build_manifest(tmp_path)


def test_combined_page_does_not_satisfy_dedicated_positive_requirement(tmp_path: Path) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list)
    first = pages[0]
    replacement = pages[8]
    assert isinstance(first, dict) and isinstance(replacement, dict)
    first_coverage = first["authoringCoverage"]
    replacement_coverage = replacement["authoringCoverage"]
    assert isinstance(first_coverage, dict) and isinstance(replacement_coverage, dict)
    family = POSITIVE_FAMILIES[0]
    first_coverage["positiveFamilies"] = []
    first_coverage["primaryPositiveFamily"] = None
    replacement_coverage["positiveFamilies"] = [family]
    replacement_coverage["primaryPositiveFamily"] = family
    _write_unsealed_corpus(tmp_path, design)
    with pytest.raises(ContractError, match=family):
        build_manifest(tmp_path)


def test_duplicate_qualification_pair_id_is_rejected(tmp_path: Path) -> None:
    _write_unsealed_corpus(tmp_path)
    path = _annotation_path(tmp_path, 0)
    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and len(pairs) >= 2
    assert isinstance(pairs[0], dict) and isinstance(pairs[1], dict)
    pairs[1]["id"] = pairs[0]["id"]
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="duplicate qualification pair"):
        load_annotation(path)


def test_duplicate_qualification_pair_endpoints_are_rejected(tmp_path: Path) -> None:
    _write_unsealed_corpus(tmp_path)
    path = _annotation_path(tmp_path, 0)
    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and len(pairs) >= 2
    assert isinstance(pairs[0], dict) and isinstance(pairs[1], dict)
    pairs[1]["earlier"] = pairs[0]["earlier"]
    pairs[1]["later"] = pairs[0]["later"]
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="duplicate qualification pair"):
        load_annotation(path)


def test_pair_endpoints_must_be_scored_and_follow_gt_precedence(tmp_path: Path) -> None:
    _write_unsealed_corpus(tmp_path)
    path = _annotation_path(tmp_path, 0)
    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and isinstance(pairs[0], dict)
    pairs[0]["earlier"], pairs[0]["later"] = pairs[0]["later"], pairs[0]["earlier"]
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="ground-truth precedence"):
        load_annotation(path)

    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and isinstance(pairs[0], dict)
    pairs[0]["earlier"] = "not-scored"
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="endpoints must both be scored"):
        load_annotation(path)


def test_slices_are_nonempty_unique_canonical_and_only_safe_vocabulary(tmp_path: Path) -> None:
    _write_unsealed_corpus(tmp_path)
    path = _annotation_path(tmp_path, 0)
    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and isinstance(pairs[0], dict)
    pairs[0]["slices"] = []
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="nonempty array"):
        load_annotation(path)

    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and isinstance(pairs[0], dict)
    pairs[0]["slices"] = ["clean-control", "clean-control"]
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="duplicate slice"):
        load_annotation(path)

    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and isinstance(pairs[0], dict)
    pairs[0]["slices"] = ["clean-control", "c1-boundary-positive"]
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="canonical sorted order"):
        load_annotation(path)

    payload = _read_json(path)
    pairs = payload["qualificationPairs"]
    assert isinstance(pairs, list) and isinstance(pairs[0], dict)
    pairs[0]["slices"] = ["c3-observed-category-negative"]
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="unknown or forbidden authoring slices"):
        load_annotation(path)


def test_input_requires_at_least_two_regions_and_preserves_source_index_order(
    tmp_path: Path,
) -> None:
    _write_unsealed_corpus(tmp_path)
    path = tmp_path / "inputs" / "page-00.json"
    payload = _read_json(path)
    regions = payload["regions"]
    assert isinstance(regions, list)
    payload["regions"] = list(reversed(regions))
    write_canonical_json(path, payload)
    loaded = load_input(path)
    assert tuple(region.source_index for region in loaded.regions) == (0, 1, 2, 3)
    assert tuple(region.region_id for region in loaded.regions) == tuple(
        f"region 0:{index}" for index in range(4)
    )

    payload = _read_json(path)
    regions = payload["regions"]
    assert isinstance(regions, list)
    payload["regions"] = regions[:1]
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="at least two regions"):
        load_input(path)


def test_source_file_is_opaque_hash_bound_bytes(tmp_path: Path) -> None:
    design = _write_corpus(tmp_path)
    pages = design["pages"]
    assert isinstance(pages, list) and isinstance(pages[0], dict)
    source = tmp_path / str(pages[0]["source"])
    source.write_bytes(b"\x00not-json-not-code\xff")
    with pytest.raises(
        ContractError, match="canonical bytes or recomputed sealed content mismatch"
    ):
        validate_corpus(tmp_path)
    write_manifest(tmp_path)
    assert validate_corpus(tmp_path).total_qualification_pairs == 120


def test_bad_hash_and_missing_extra_inventory_are_rejected(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = _read_json(manifest_path)
    pages = manifest["pages"]
    assert isinstance(pages, list) and isinstance(pages[0], dict)
    image = pages[0]["image"]
    assert isinstance(image, dict)
    image["sha256"] = "0" * 64
    write_canonical_json(manifest_path, manifest)
    with pytest.raises(
        ContractError, match="canonical bytes or recomputed sealed content mismatch"
    ):
        validate_corpus(tmp_path)

    write_manifest(tmp_path)
    (tmp_path / "extra.bin").write_bytes(b"extra")
    with pytest.raises(ContractError, match="corpus file inventory mismatch"):
        validate_corpus(tmp_path)


def test_page_id_and_region_inventory_mismatch_are_rejected(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    input_path = tmp_path / "inputs" / "page-00.json"
    payload = _read_json(input_path)
    payload["pageId"] = "other-page"
    write_canonical_json(input_path, payload)
    with pytest.raises(ContractError, match="pageId mismatch"):
        validate_corpus(tmp_path)

    _write_corpus(tmp_path)
    annotation_path = _annotation_path(tmp_path, 0)
    payload = _read_json(annotation_path)
    reading = payload["readingOrder"]
    assert isinstance(reading, list)
    reading[0] = "different-region"
    write_canonical_json(annotation_path, payload)
    with pytest.raises(ContractError, match="input/annotation region inventory mismatch|endpoints"):
        validate_corpus(tmp_path)


def _mutate_chunk_crc(payload: bytes, chunk_type: bytes) -> bytes:
    offset = len(PNG_SIGNATURE)
    mutable = bytearray(payload)
    while offset < len(mutable):
        length = struct.unpack_from(">I", mutable, offset)[0]
        kind = bytes(mutable[offset + 4 : offset + 8])
        crc_offset = offset + 8 + length
        if kind == chunk_type:
            mutable[crc_offset] ^= 0x01
            return bytes(mutable)
        offset = crc_offset + 4
    raise AssertionError(f"chunk {chunk_type!r} not found")


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda data: b"BAD" + data[3:], "invalid PNG signature"),
        (lambda data: data[:-5], "truncated|missing IEND"),
        (lambda data: _mutate_chunk_crc(data, b"IDAT"), "CRC mismatch"),
        (lambda data: _png_bytes(interlace=1), "non-interlaced 8-bit RGB"),
        (lambda data: data + b"trailing", "trailing bytes after IEND"),
    ],
)
def test_strict_png_contract_rejects_corruption(
    tmp_path: Path,
    mutation: object,
    expected: str,
) -> None:
    path = tmp_path / "image.png"
    data = _png_bytes()
    assert callable(mutation)
    path.write_bytes(mutation(data))
    with pytest.raises(ContractError, match=expected):
        validate_rgb_png(path)


def test_strict_png_requires_complete_zlib_stream_and_exact_decoded_size(tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    good = _png_bytes()
    offset = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(good):
        length = struct.unpack_from(">I", good, offset)[0]
        kind = good[offset + 4 : offset + 8]
        data = good[offset + 8 : offset + 8 + length]
        chunks.append((kind, data))
        offset += 12 + length
    rebuilt = PNG_SIGNATURE
    for kind, data in chunks:
        if kind == b"IDAT":
            data = data[:-2]
        rebuilt += _chunk(kind, data)
    path.write_bytes(rebuilt)
    with pytest.raises(ContractError, match="zlib stream"):
        validate_rgb_png(path)


def test_role_paths_reject_traversal_backslash_and_windows_drive(tmp_path: Path) -> None:
    for unsafe in ("../escape.bin", "source\\alias.bin", "C:/outside.bin"):
        design = _design()
        pages = design["pages"]
        assert isinstance(pages, list) and isinstance(pages[0], dict)
        pages[0]["source"] = unsafe
        write_canonical_json(tmp_path / "corpus-design.json", design)
        with pytest.raises(ContractError, match="safe normalized POSIX relative path"):
            load_design(tmp_path / "corpus-design.json")


def test_leaf_and_ancestor_symlinks_are_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support required")
    design = _write_unsealed_corpus(tmp_path)
    pages = design["pages"]
    assert isinstance(pages, list) and isinstance(pages[0], dict)
    source = tmp_path / str(pages[0]["source"])
    target = tmp_path / "real-source.bin"
    source.rename(target)
    source.symlink_to(target)
    with pytest.raises(ContractError, match="symlinked sealed path|symlinked sealed file"):
        build_manifest(tmp_path)

    root = tmp_path / "second"
    design = _write_unsealed_corpus(root)
    pages = design["pages"]
    assert isinstance(pages, list) and isinstance(pages[0], dict)
    source_dir = root / "source"
    outside = tmp_path / "outside-source"
    outside.mkdir()
    for child in source_dir.iterdir():
        child.rename(outside / child.name)
    source_dir.rmdir()
    source_dir.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ContractError, match="symlinked sealed"):
        build_manifest(root)


def test_manifest_symlink_is_never_followed_or_overwritten(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlink support required")
    _write_unsealed_corpus(tmp_path)
    outside = tmp_path.parent / "outside-manifest-target.json"
    outside.write_bytes(b"sentinel")
    (tmp_path / "manifest.json").symlink_to(outside)
    with pytest.raises(ContractError, match="symlinked sealed"):
        write_manifest(tmp_path)
    assert outside.read_bytes() == b"sentinel"


def test_manifest_must_be_exact_canonical_recomputed_bytes_and_rewrite_is_stable(
    tmp_path: Path,
) -> None:
    _write_unsealed_corpus(tmp_path)
    path = write_manifest(tmp_path)
    first = path.read_bytes()
    assert first == canonical_json_bytes(build_manifest(tmp_path))
    validate_corpus(tmp_path)

    parsed = json.loads(first)
    path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    with pytest.raises(ContractError, match="canonical bytes"):
        validate_corpus(tmp_path)

    write_manifest(tmp_path)
    second = path.read_bytes()
    assert second == first
    validate_corpus(tmp_path)


def test_author_safe_surface_is_exact_and_has_no_forbidden_dependencies() -> None:
    actual = {path.name for path in PACKAGE_ROOT.iterdir() if path.is_file()}
    assert actual == AUTHOR_SAFE_FILES
    forbidden_import_prefixes = (
        "scripts.reading_order_v2",
        "scripts.reading_order_post_v2_calibration",
        "scripts.reading_order_post_v2_qualification",
        "mangasensei.ocr",
    )
    forbidden_text = (
        "reading-order-v2/heldout-v1",
        "reading-order-post-v2/heldout-v2",
        "mangasensei-reading-order-heldout-v2",
        "c3-zero-multiple-anchor-negative",
        "c3-zero-multiple-companion-negative",
        "c3-invalid-topology-negative",
        "c3-insufficient-visible-support-negative",
        "Q101",
        "Q124",
        "H01",
        "H16",
    )
    for filename in AUTHOR_SAFE_FILES:
        path = PACKAGE_ROOT / filename
        text = path.read_text(encoding="utf-8")
        for token in forbidden_text:
            assert token not in text, f"{path}: forbidden observed/historical token {token!r}"
        if path.suffix != ".py":
            continue
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = [node.module]
            else:
                continue
            assert not any(
                name.startswith(prefix) for name in names for prefix in forbidden_import_prefixes
            ), f"{path}: forbidden author-facing import {names}"


def test_reader_contract_marks_safe_surface_and_tests_as_forbidden() -> None:
    readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "SAFE FOR ISOLATED AUTHOR TO READ" in readme
    assert "tests/reading_order_v3_authoring/" in readme
    assert "must not open" in readme.lower()
    assert "minimumPageCount = 24" in readme
    assert "c3_rejection_pages >= 8" in readme
    assert "qualificationPairs" in readme
    assert "layoutTags" in readme
    assert "not an author field" in readme
