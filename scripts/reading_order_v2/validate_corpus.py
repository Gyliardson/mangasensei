from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from PIL import Image

from .contracts import PAGE_IDS, ReadingOrderV2ContractError, load_annotation, load_manifest
from .validate_design import validate_design


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_member(root: Path, relative: str, expected_prefix: str) -> Path:
    if relative.startswith(("/", "\\")) or "\\" in relative:
        raise ReadingOrderV2ContractError(f"unsafe corpus path {relative!r}")
    parts = Path(relative).parts
    if ".." in parts or not parts or parts[0] != expected_prefix:
        raise ReadingOrderV2ContractError(f"unexpected corpus path {relative!r}")
    return root / relative


def validate_corpus(root: Path) -> None:
    design = root / "corpus-design.json"
    validate_design(design)
    manifest_path = root / "manifest.json"
    manifest = load_manifest(manifest_path)
    if manifest.design_sha256 != _sha256(design):
        raise ReadingOrderV2ContractError("manifest design SHA-256 mismatch")

    a_pairs = 0
    b_pairs = 0
    a_pages: set[str] = set()
    b_pages: set[str] = set()
    clean_pages = 0
    fallback_pages = 0
    open_pages = 0
    for page in manifest.pages:
        expected_relative = {
            "source": f"source/{page.page_id}.svg",
            "image": f"images/{page.page_id}.png",
            "input": f"inputs/{page.page_id}.json",
            "annotation": f"annotations/{page.page_id}.json",
        }
        actual_relative = {
            "source": page.source_file,
            "image": page.image_file,
            "input": page.input_file,
            "annotation": page.annotation_file,
        }
        if actual_relative != expected_relative:
            raise ReadingOrderV2ContractError(
                f"{page.page_id}: manifest paths must use the frozen directory layout"
            )
        paths = {
            kind: _safe_member(root, relative, expected_relative[kind].split("/", 1)[0])
            for kind, relative in actual_relative.items()
        }
        expected_hashes = {
            "source": page.source_sha256,
            "image": page.image_sha256,
            "input": page.input_sha256,
            "annotation": page.annotation_sha256,
        }
        for kind, path in paths.items():
            if not path.is_file() or path.is_symlink() or _sha256(path) != expected_hashes[kind]:
                raise ReadingOrderV2ContractError(f"{page.page_id}: {kind} integrity mismatch")
        with Image.open(paths["image"]) as image:
            if image.format != "PNG" or image.mode != "RGB" or image.size != (1440, 2048):
                raise ReadingOrderV2ContractError(f"{page.page_id}: image contract mismatch")
        annotation = load_annotation(paths["annotation"])
        if annotation.page_id != page.page_id or annotation.image_sha256 != page.image_sha256:
            raise ReadingOrderV2ContractError(f"{page.page_id}: annotation identity mismatch")
        page_a = [pair for pair in annotation.qualification_pairs if "A" in pair.slices]
        page_b = [pair for pair in annotation.qualification_pairs if "B" in pair.slices]
        a_pairs += len(page_a)
        b_pairs += len(page_b)
        if page_a:
            a_pages.add(page.page_id)
        if page_b:
            b_pages.add(page.page_id)
        tags = set(annotation.layout_tags)
        clean_pages += "clean-control" in tags
        fallback_pages += "intentional-fallback" in tags
        open_pages += "open-incomplete-frame" in tags
    if tuple(page.page_id for page in manifest.pages) != PAGE_IDS:
        raise ReadingOrderV2ContractError("held-out corpus must contain exactly H01..H16")
    failures = []
    if a_pairs < 12 or len(a_pages) < 5:
        failures.append("A qualification minimum")
    if b_pairs < 12 or len(b_pages) < 5:
        failures.append("B qualification minimum")
    if clean_pages < 4:
        failures.append("clean ordinary control minimum")
    if fallback_pages < 2:
        failures.append("intentional fallback minimum")
    if open_pages < 2:
        failures.append("open/incomplete-frame minimum")
    if failures:
        raise ReadingOrderV2ContractError(f"corpus coverage minima failed: {failures!r}")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(args[0]) if args else Path("assets/reading-order-v2/heldout-v1")
    validate_corpus(root)
    print(f"validated {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
