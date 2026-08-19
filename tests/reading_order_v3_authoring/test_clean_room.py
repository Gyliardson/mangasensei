from __future__ import annotations

import ast
import json
import os
import struct
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from scripts.reading_order_v3_authoring import (
    AUTHORING_SLICES,
    C3_REJECTION_FAMILY,
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
from scripts.reading_order_v3_authoring.contracts import (
    load_annotation,
    load_design,
    load_input,
    validate_authoring_coverage,
)

from ._fixtures import SIG, _annotations, _chunk, _design, _page, _png, _write

ROOT = Path(__file__).resolve().parents[2] / "scripts" / "reading_order_v3_authoring"
SAFE = {"README.md", "__init__.py", "__main__.py", "canonical.py", "contracts.py", "validate.py"}


def test_valid_fixture_and_arbitrary_ids(tmp_path: Path) -> None:
    ids = tuple(f"chapter.alpha-{i}" for i in range(24))
    design = _design(ids)
    design["corpusId"] = "independent.identity"
    design["version"] = "future+draft"
    _write(tmp_path, design)
    s = validate_corpus(tmp_path)
    assert s.total_qualification_pairs == 120
    assert s.total_scored_regions == 96
    assert len(s.c3_rejection_pages) == 8
    assert set(s.dedicated_positive_pages) == set(POSITIVE_FAMILIES)
    for name, minimum in SLICE_MINIMA.items():
        assert s.slice_pair_counts[name] >= minimum["minPairs"]
        assert s.slice_page_counts[name] >= minimum["minPages"]


def test_page_pair_scored_minima(tmp_path: Path) -> None:
    d = _design()
    d["pages"] = d["pages"][:23]
    write_canonical_json(tmp_path / "corpus-design.json", d)
    with pytest.raises(ContractError, match="at least 24 pages"):
        load_design(tmp_path / "corpus-design.json")
    root = tmp_path / "full"
    _write(root, seal=False)
    design, anns = _annotations(root)
    last = anns["page-23"]
    anns["page-23"] = replace(last, qualification_pairs=last.qualification_pairs[:-1])
    with pytest.raises(ContractError, match="qualification pairs below"):
        validate_authoring_coverage(design, anns)
    design, anns = _annotations(root)
    last = anns["page-23"]
    anns["page-23"] = replace(last, reading_order=last.reading_order[:-1])
    with pytest.raises(ContractError, match="scored regions below"):
        validate_authoring_coverage(design, anns)


@pytest.mark.parametrize("slice_name", AUTHORING_SLICES)
def test_all_slice_and_design_role_minima(tmp_path: Path, slice_name: str) -> None:
    _write(tmp_path, seal=False)
    design, anns = _annotations(tmp_path)
    for page_id, ann in tuple(anns.items()):
        pairs = tuple(
            replace(p, slices=tuple(s for s in p.slices if s != slice_name))
            for p in ann.qualification_pairs
        )
        anns[page_id] = replace(ann, qualification_pairs=pairs)
    with pytest.raises(ContractError):
        validate_authoring_coverage(design, anns)


def test_c3_eight_unique_and_combined_not_dedicated(tmp_path: Path) -> None:
    d = _design()
    for i, page in enumerate(cast(list[dict[str, Any]], d["pages"])):
        cast(dict[str, Any], page["authoringCoverage"])["c3Rejection"] = 16 <= i <= 22
    _write(tmp_path, d, seal=False)
    with pytest.raises(ContractError, match=rf"{C3_REJECTION_FAMILY} requires at least 8"):
        build_manifest(tmp_path)
    root = tmp_path / "combined"
    d = _design()
    pages = cast(list[dict[str, Any]], d["pages"])
    cast(dict[str, Any], pages[0]["authoringCoverage"])["positiveFamilies"] = []
    cast(dict[str, Any], pages[0]["authoringCoverage"])["primaryPositiveFamily"] = None
    cast(dict[str, Any], pages[8]["authoringCoverage"])["positiveFamilies"] = [POSITIVE_FAMILIES[0]]
    replacement_coverage = cast(dict[str, Any], pages[8]["authoringCoverage"])
    replacement_coverage["primaryPositiveFamily"] = POSITIVE_FAMILIES[0]
    _write(root, d, seal=False)
    with pytest.raises(ContractError, match=POSITIVE_FAMILIES[0]):
        build_manifest(root)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("id", "", "nonempty string"),
        ("slices", [], "nonempty array"),
        ("slices", ["clean-control", "clean-control"], "duplicate slice"),
        ("slices", ["clean-control", "c1-boundary-positive"], "canonical sorted"),
        ("slices", ["c3-observed-category-negative"], "unknown or forbidden"),
    ],
)
def test_pair_schema_and_safe_slice_vocabulary(
    tmp_path: Path, field: str, value: Any, message: str
) -> None:
    _, ann = _page("free-page", 0)
    cast(list[dict[str, Any]], ann["qualificationPairs"])[0][field] = value
    path = tmp_path / "ann.json"
    write_canonical_json(path, ann)
    with pytest.raises(ContractError, match=message):
        load_annotation(path)


def test_pair_uniqueness_scored_endpoints_precedence(tmp_path: Path) -> None:
    path = tmp_path / "ann.json"
    _, ann = _page("free-page", 0)
    pairs = cast(list[dict[str, Any]], ann["qualificationPairs"])
    pairs[1]["id"] = pairs[0]["id"]
    write_canonical_json(path, ann)
    with pytest.raises(ContractError, match="duplicate qualification pair"):
        load_annotation(path)
    _, ann = _page("free-page", 0)
    pairs = cast(list[dict[str, Any]], ann["qualificationPairs"])
    pairs[1]["earlier"], pairs[1]["later"] = pairs[0]["earlier"], pairs[0]["later"]
    write_canonical_json(path, ann)
    with pytest.raises(ContractError, match="duplicate qualification pair"):
        load_annotation(path)
    _, ann = _page("free-page", 0)
    pair = cast(list[dict[str, Any]], ann["qualificationPairs"])[0]
    pair["earlier"], pair["later"] = pair["later"], pair["earlier"]
    write_canonical_json(path, ann)
    with pytest.raises(ContractError, match="ground-truth precedence"):
        load_annotation(path)
    _, ann = _page("free-page", 0)
    cast(list[dict[str, Any]], ann["qualificationPairs"])[0]["earlier"] = "not-scored"
    write_canonical_json(path, ann)
    with pytest.raises(ContractError, match="endpoints must both be scored"):
        load_annotation(path)


def test_input_mapping_contract_and_region_id_freedom(tmp_path: Path) -> None:
    inp, _ = _page("free-page", 0)
    regions = cast(list[dict[str, Any]], inp["regions"])
    regions[0]["regionId"] = "自由 region / punctuation:#"
    inp["regions"] = list(reversed(regions))
    path = tmp_path / "input.json"
    write_canonical_json(path, inp)
    loaded = load_input(path)
    assert tuple(r.source_index for r in loaded.regions) == (0, 1, 2, 3)
    assert loaded.regions[0].region_id == "自由 region / punctuation:#"
    inp["regions"] = regions[:1]
    write_canonical_json(path, inp)
    with pytest.raises(ContractError, match="at least two regions"):
        load_input(path)


def test_source_hash_inventory_and_page_region_binding(tmp_path: Path) -> None:
    _write(tmp_path)
    source = tmp_path / "source" / "page-00.bin"
    source.write_bytes(b"\0opaque\xff")
    with pytest.raises(ContractError, match="canonical bytes"):
        validate_corpus(tmp_path)
    write_manifest(tmp_path)
    (tmp_path / "extra.bin").write_bytes(b"extra")
    with pytest.raises(ContractError, match="inventory mismatch"):
        validate_corpus(tmp_path)
    root = tmp_path / "binding"
    _write(root)
    path = root / "inputs" / "page-00.json"
    payload = json.loads(path.read_text())
    payload["pageId"] = "other-page"
    write_canonical_json(path, payload)
    with pytest.raises(ContractError, match="pageId mismatch"):
        validate_corpus(root)


def _bad_crc(data: bytes) -> bytes:
    out = bytearray(data)
    off = len(SIG)
    while off < len(out):
        length = struct.unpack_from(">I", out, off)[0]
        if bytes(out[off + 4 : off + 8]) == b"IDAT":
            out[off + 8 + length] ^= 1
            return bytes(out)
        off += 12 + length
    raise AssertionError("IDAT missing")


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"BAD" + _png()[3:], "invalid PNG signature"),
        (_png()[:-5], "truncated|missing IEND"),
        (_bad_crc(_png()), "CRC mismatch"),
        (_png(interlace=1), "non-interlaced 8-bit RGB"),
        (_png() + b"trailing", "trailing bytes after IEND"),
    ],
)
def test_strict_png(data: bytes, message: str, tmp_path: Path) -> None:
    path = tmp_path / "image.png"
    path.write_bytes(data)
    with pytest.raises(ContractError, match=message):
        validate_rgb_png(path)


def test_truncated_zlib(tmp_path: Path) -> None:
    good = _png()
    off = len(SIG)
    rebuilt = SIG
    while off < len(good):
        length = struct.unpack_from(">I", good, off)[0]
        kind = good[off + 4 : off + 8]
        data = good[off + 8 : off + 8 + length]
        rebuilt += _chunk(kind, data[:-2] if kind == b"IDAT" else data)
        off += 12 + length
    path = tmp_path / "image.png"
    path.write_bytes(rebuilt)
    with pytest.raises(ContractError, match="zlib stream"):
        validate_rgb_png(path)


@pytest.mark.parametrize("unsafe", ["../escape.bin", "source\\alias.bin", "C:/outside.bin"])
def test_unsafe_paths(unsafe: str, tmp_path: Path) -> None:
    d = _design()
    cast(list[dict[str, Any]], d["pages"])[0]["source"] = unsafe
    write_canonical_json(tmp_path / "corpus-design.json", d)
    with pytest.raises(ContractError, match="safe normalized POSIX relative path"):
        load_design(tmp_path / "corpus-design.json")


def test_leaf_ancestor_manifest_symlinks(tmp_path: Path) -> None:
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    _write(tmp_path, seal=False)
    source = tmp_path / "source" / "page-00.bin"
    real = tmp_path / "real.bin"
    source.rename(real)
    source.symlink_to(real)
    with pytest.raises(ContractError, match="symlinked sealed"):
        build_manifest(tmp_path)
    root = tmp_path / "ancestor"
    _write(root, seal=False)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    for child in (root / "source").iterdir():
        child.rename(outside_dir / child.name)
    (root / "source").rmdir()
    (root / "source").symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(ContractError, match="symlinked sealed"):
        build_manifest(root)
    root = tmp_path / "manifest"
    _write(root, seal=False)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"sentinel")
    (root / "manifest.json").symlink_to(outside)
    with pytest.raises(ContractError, match="symlinked sealed"):
        write_manifest(root)
    assert outside.read_bytes() == b"sentinel"


def test_manifest_canonical_bytes_and_stable_rewrite(tmp_path: Path) -> None:
    _write(tmp_path, seal=False)
    path = write_manifest(tmp_path)
    first = path.read_bytes()
    assert first == canonical_json_bytes(build_manifest(tmp_path))
    path.write_text(json.dumps(json.loads(first), indent=2))
    with pytest.raises(ContractError, match="canonical bytes"):
        validate_corpus(tmp_path)
    write_manifest(tmp_path)
    assert path.read_bytes() == first


def test_exact_author_surface_and_anti_leak() -> None:
    assert {p.name for p in ROOT.iterdir() if p.is_file()} == SAFE
    forbidden_imports = (
        "scripts.reading_order_v2",
        "scripts.reading_order_post_v2_calibration",
        "scripts.reading_order_post_v2_qualification",
        "mangasensei.ocr",
    )
    forbidden = (
        "mangasensei-reading-order-heldout-v2",
        "c3-zero-multiple-anchor-negative",
        "c3-zero-multiple-companion-negative",
        "c3-invalid-topology-negative",
        "c3-insufficient-visible-support-negative",
        "Q101", "Q124", "H01", "H16",
    )
    for name in SAFE:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden)
        if path.suffix != ".py":
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n.startswith(p) for n in names for p in forbidden_imports)


def test_readme_safe_surface_and_contract() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in (
        "SAFE FOR ISOLATED AUTHOR TO READ",
        "tests/reading_order_v3_authoring/",
        "minimumPageCount = 24",
        "c3_rejection_pages >= 8",
        "qualificationPairs",
        "layoutTags",
        "not an author field",
    ):
        assert required in text
