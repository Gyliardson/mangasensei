from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from PIL import Image

from scripts.reading_order_v3_authoring import (
    ANNOTATION_SCHEMA_VERSION,
    AUTHORSHIP_BOUNDARY,
    C3_REJECTION_FAMILY,
    DESIGN_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
    POSITIVE_FAMILIES,
    ContractError,
    build_manifest,
    validate_corpus,
    write_canonical_json,
    write_manifest,
)
from scripts.reading_order_v3_authoring.contracts import load_design, validate_authoring_coverage

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "scripts" / "reading_order_v3_authoring"


def _page_record(page_id: str, family: str | None, *, c3: bool = False) -> dict[str, object]:
    positive = [] if family is None else [family]
    return {
        "pageId": page_id,
        "source": f"sources/{page_id}.source",
        "image": f"images/{page_id}.png",
        "input": f"inputs/{page_id}.json",
        "annotation": f"annotations/{page_id}.json",
        "authoringCoverage": {
            "positiveFamilies": positive,
            "primaryPositiveFamily": family,
            "c3Rejection": c3,
        },
    }


def _design(
    *,
    corpus_id: str = "novel-corpus-alpha",
    version: str = "v-next.7",
    page_ids: tuple[str, ...] | None = None,
) -> dict[str, object]:
    ids = page_ids or tuple(f"synthetic-{index}" for index in range(len(POSITIVE_FAMILIES)))
    pages = [
        _page_record(page_id, family)
        for page_id, family in zip(ids, POSITIVE_FAMILIES, strict=True)
    ]
    pages.append(_page_record("synthetic-c3-negative", None, c3=True))
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
        "pages": pages,
    }


def _write_page(
    root: Path,
    page: dict[str, object],
    *,
    region_ids: tuple[str, ...] = ("r-a",),
) -> None:
    page_id = str(page["pageId"])
    source = root / str(page["source"])
    image = root / str(page["image"])
    input_path = root / str(page["input"])
    annotation = root / str(page["annotation"])
    source.parent.mkdir(parents=True, exist_ok=True)
    image.parent.mkdir(parents=True, exist_ok=True)
    input_path.parent.mkdir(parents=True, exist_ok=True)
    annotation.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic-structural-source\n")
    Image.new("RGB", (7, 5)).save(image, format="PNG")
    regions = [
        {
            "regionId": region_id,
            "sourceIndex": index,
            "lines": [[[index, 0], [index + 1, 0], [index + 1, 1], [index, 1]]],
            "angle": 0.0,
        }
        for index, region_id in enumerate(region_ids)
    ]
    write_canonical_json(
        input_path,
        {
            "schemaVersion": INPUT_SCHEMA_VERSION,
            "pageId": page_id,
            "width": 7,
            "height": 5,
            "regions": regions,
        },
    )
    write_canonical_json(
        annotation,
        {
            "schemaVersion": ANNOTATION_SCHEMA_VERSION,
            "pageId": page_id,
            "readingOrder": list(region_ids),
            "unscoredRegionIds": [],
        },
    )


def _write_corpus(root: Path, design: dict[str, object] | None = None) -> dict[str, object]:
    payload = design or _design()
    write_canonical_json(root / "corpus-design.json", payload)
    pages = payload["pages"]
    assert isinstance(pages, list)
    for page in pages:
        assert isinstance(page, dict)
        _write_page(root, page)
    write_manifest(root)
    return payload


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_arbitrary_new_identity_version_and_page_ids_are_accepted(tmp_path: Path) -> None:
    page_ids = tuple(f"page-{letter}" for letter in "abcdefgh")
    _write_corpus(
        tmp_path,
        _design(
            corpus_id="independent-ro-corpus",
            version="2026.experimental+1",
            page_ids=page_ids,
        ),
    )
    summary = validate_corpus(tmp_path)
    assert set(summary.dedicated_positive_pages) == set(POSITIVE_FAMILIES)
    assert summary.c3_rejection_pages == ("synthetic-c3-negative",)


def test_duplicate_page_ids_are_rejected(tmp_path: Path) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list)
    assert isinstance(pages[1], dict)
    pages[1]["pageId"] = pages[0]["pageId"]
    write_canonical_json(tmp_path / "corpus-design.json", design)
    with pytest.raises(ContractError, match="duplicate page ID"):
        load_design(tmp_path / "corpus-design.json")


def test_bad_hash_is_rejected(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    manifest = _read_json(tmp_path / "manifest.json")
    pages = manifest["pages"]
    assert isinstance(pages, list)
    assert isinstance(pages[0], dict)
    image = pages[0]["image"]
    assert isinstance(image, dict)
    image["sha256"] = "0" * 64
    write_canonical_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(
        ContractError, match="manifest: identity, role binding, hash, or inventory mismatch"
    ):
        validate_corpus(tmp_path)


@pytest.mark.parametrize("case", ["missing", "extra"])
def test_missing_or_extra_inventory_is_rejected(tmp_path: Path, case: str) -> None:
    design = _write_corpus(tmp_path)
    pages = design["pages"]
    assert isinstance(pages, list)
    assert isinstance(pages[0], dict)
    if case == "missing":
        (tmp_path / str(pages[0]["source"])).unlink()
    else:
        (tmp_path / "undeclared.bin").write_bytes(b"extra")
    with pytest.raises(ContractError, match="corpus file inventory mismatch|regular file required"):
        validate_corpus(tmp_path)


def test_page_id_mismatch_is_rejected_before_hash_validation(tmp_path: Path) -> None:
    design = _write_corpus(tmp_path)
    pages = design["pages"]
    assert isinstance(pages, list)
    assert isinstance(pages[0], dict)
    input_path = tmp_path / str(pages[0]["input"])
    payload = _read_json(input_path)
    payload["pageId"] = "different-page"
    write_canonical_json(input_path, payload)
    with pytest.raises(ContractError, match="pageId mismatch"):
        validate_corpus(tmp_path)


def test_region_inventory_mismatch_is_rejected(tmp_path: Path) -> None:
    design = _write_corpus(tmp_path)
    pages = design["pages"]
    assert isinstance(pages, list)
    assert isinstance(pages[0], dict)
    annotation_path = tmp_path / str(pages[0]["annotation"])
    payload = _read_json(annotation_path)
    payload["readingOrder"] = ["different-region"]
    write_canonical_json(annotation_path, payload)
    with pytest.raises(ContractError, match="input/annotation region inventory mismatch"):
        validate_corpus(tmp_path)


def test_malformed_reading_order_is_rejected(tmp_path: Path) -> None:
    design = _write_corpus(tmp_path)
    pages = design["pages"]
    assert isinstance(pages, list)
    assert isinstance(pages[0], dict)
    annotation_path = tmp_path / str(pages[0]["annotation"])
    payload = _read_json(annotation_path)
    payload["readingOrder"] = ["r-a", "r-a"]
    write_canonical_json(annotation_path, payload)
    with pytest.raises(ContractError, match="readingOrder: duplicate region ID"):
        validate_corpus(tmp_path)


def test_duplicate_regions_are_rejected(tmp_path: Path) -> None:
    design = _write_corpus(tmp_path)
    pages = design["pages"]
    assert isinstance(pages, list)
    assert isinstance(pages[0], dict)
    input_path = tmp_path / str(pages[0]["input"])
    payload = _read_json(input_path)
    regions = payload["regions"]
    assert isinstance(regions, list)
    duplicate = dict(regions[0])
    duplicate["sourceIndex"] = 1
    regions.append(duplicate)
    write_canonical_json(input_path, payload)
    with pytest.raises(ContractError, match="duplicate region identity/index"):
        validate_corpus(tmp_path)


def test_missing_dedicated_positive_family_is_rejected(tmp_path: Path) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list)
    pages.pop(0)
    write_canonical_json(tmp_path / "corpus-design.json", design)
    parsed = load_design(tmp_path / "corpus-design.json")
    with pytest.raises(ContractError, match="missing dedicated positive families"):
        validate_authoring_coverage(parsed)


def test_combined_only_coverage_does_not_satisfy_dedicated_requirement(tmp_path: Path) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list)
    first = pages[0]
    assert isinstance(first, dict)
    coverage = first["authoringCoverage"]
    assert isinstance(coverage, dict)
    coverage["positiveFamilies"] = [POSITIVE_FAMILIES[0], POSITIVE_FAMILIES[1]]
    coverage["primaryPositiveFamily"] = POSITIVE_FAMILIES[0]
    write_canonical_json(tmp_path / "corpus-design.json", design)
    parsed = load_design(tmp_path / "corpus-design.json")
    with pytest.raises(ContractError, match=POSITIVE_FAMILIES[0]):
        validate_authoring_coverage(parsed)


def test_c3_rejection_is_counted_once_per_page(tmp_path: Path) -> None:
    design = _design()
    pages = design["pages"]
    assert isinstance(pages, list)
    c3_page = pages[-1]
    assert isinstance(c3_page, dict)
    _write_corpus(tmp_path, design)
    _write_page(tmp_path, c3_page, region_ids=("r-a", "r-b", "r-c"))
    write_manifest(tmp_path)
    summary = validate_corpus(tmp_path)
    assert C3_REJECTION_FAMILY == "c3_rejection_pages"
    assert summary.c3_rejection_pages == (str(c3_page["pageId"]),)


def test_no_historical_dimensions_page_count_or_id_pattern_is_required(tmp_path: Path) -> None:
    page_ids = tuple(f"unit-{index}-x" for index in range(len(POSITIVE_FAMILIES)))
    design = _design(corpus_id="fresh-identity", version="z9", page_ids=page_ids)
    _write_corpus(tmp_path, design)
    assert validate_corpus(tmp_path).c3_rejection_pages


def test_build_manifest_is_deterministic_and_binds_exact_roles(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    first = build_manifest(tmp_path)
    second = build_manifest(tmp_path)
    assert first == second
    pages = first["pages"]
    assert isinstance(pages, list)
    assert all(
        isinstance(page, dict) and set(page) == {"pageId", "source", "image", "input", "annotation"}
        for page in pages
    )


def test_clean_room_python_has_no_forbidden_scientific_or_historical_dependency() -> None:
    forbidden_import_prefixes = (
        "scripts.reading_order_v2",
        "scripts.reading_order_post_v2_calibration",
        "scripts.reading_order_post_v2_qualification",
        "mangasensei.ocr.diagnostics",
    )
    forbidden_text = (
        "assets/reading-order-v2/heldout",
        "scripts/reading_order_v2/validate_corpus",
        "reading_order_post_v2_calibration",
        "reading_order_post_v2_qualification",
        "mangasensei-reading-order-heldout-v2",
        "H01",
        "Q101",
        "Q124",
    )
    for path in PACKAGE_ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden_text:
            assert token not in text, f"{path}: forbidden clean-room dependency/token {token!r}"
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
            ), f"{path}: forbidden import {names}"


def test_reader_contract_is_marked_safe_and_only_lists_forbidden_paths_as_boundaries() -> None:
    readme = (PACKAGE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "SAFE FOR ISOLATED AUTHOR TO READ" in readme
    assert "assets/reading-order-v2/heldout*/" in readme
    assert "scripts/reading_order_v2/validate_corpus.py" in readme
    assert "scripts/reading_order_post_v2_qualification/" in readme
    assert "mangasensei-reading-order-heldout-v2" not in readme
