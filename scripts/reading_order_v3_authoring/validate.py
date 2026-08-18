from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .canonical import file_record, sha256_path, write_canonical_json
from .contracts import (
    MANIFEST_SCHEMA_VERSION,
    ContractError,
    CorpusDesign,
    CoverageSummary,
    load_annotation,
    load_design,
    load_input,
    validate_authoring_coverage,
)

DESIGN_FILE = "corpus-design.json"
MANIFEST_FILE = "manifest.json"


def _required_paths(design: CorpusDesign) -> set[str]:
    paths = {DESIGN_FILE}
    for page in design.pages:
        paths.update((page.source, page.image, page.input, page.annotation))
    return paths


def _actual_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != MANIFEST_FILE
    }


def _require_regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.is_file():
        raise ContractError(f"{relative}: regular file required")
    return path


def _validate_page_contents(root: Path, design: CorpusDesign) -> None:
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

        try:
            with Image.open(image_path) as image:
                image.load()
                if image.format != "PNG" or image.mode != "RGB":
                    raise ContractError(f"{page.page_id}: image must be RGB PNG")
                if image.size != (page_input.width, page_input.height):
                    raise ContractError(f"{page.page_id}: image/input dimension mismatch")
        except (OSError, UnidentifiedImageError) as exc:
            raise ContractError(f"{page.page_id}: invalid PNG image") from exc


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
    design = load_design(root / DESIGN_FILE)
    _validate_exact_files(root, design)
    _validate_page_contents(root, design)
    validate_authoring_coverage(design)
    return _manifest_data(root, design)


def write_manifest(root: Path) -> Path:
    manifest = build_manifest(root)
    path = root / MANIFEST_FILE
    write_canonical_json(path, manifest)
    return path


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path}: invalid manifest JSON") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{path}: manifest object required")
    return value


def validate_corpus(root: Path) -> CoverageSummary:
    design = load_design(root / DESIGN_FILE)
    _validate_exact_files(root, design)
    _validate_page_contents(root, design)
    coverage = validate_authoring_coverage(design)
    expected_manifest = _manifest_data(root, design)
    actual_manifest = _load_manifest(root / MANIFEST_FILE)
    if actual_manifest != expected_manifest:
        raise ContractError("manifest: identity, role binding, hash, or inventory mismatch")
    return coverage
