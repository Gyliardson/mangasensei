from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from .canonical import sha256_path
from .contracts import (
    CORPUS_ID,
    CORPUS_VERSION,
    PAGE_IDS,
    ContractError,
    load_arm_input,
    load_ground_truth,
    validate_corpus_design,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "assets" / "reading-order-v2" / "heldout-v1"


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ContractError(f"{path}: expected object")
    return value


def validate_corpus(root: Path = CORPUS_ROOT) -> None:
    design = _object(root / "corpus-design.json")
    validate_corpus_design(design)
    manifest_path = root / "manifest.json"
    manifest = _object(manifest_path)
    if manifest.get("schemaVersion") != "reading-order-v2-manifest-v1":
        raise ContractError("manifest: bad schema version")
    if manifest.get("corpusId") != CORPUS_ID or manifest.get("version") != CORPUS_VERSION:
        raise ContractError("manifest: wrong corpus identity")
    if manifest.get("designSha256") != sha256_path(root / "corpus-design.json"):
        raise ContractError("manifest: corpus-design SHA mismatch")
    pages = manifest.get("pages")
    page_ids = (
        [page.get("id") for page in pages if isinstance(page, dict)]
        if isinstance(pages, list)
        else []
    )
    if page_ids != list(PAGE_IDS):
        raise ContractError("manifest: pages must be exactly H01..H16")

    a_pairs = 0
    b_pairs = 0
    a_pages: set[str] = set()
    b_pages: set[str] = set()
    clean_pages = 0
    fallback_pages = 0
    open_pages = 0
    for page_record in pages:
        assert isinstance(page_record, dict)
        page_id = page_record["id"]
        expected_paths = {
            "source": f"source/{page_id}.svg",
            "image": f"images/{page_id}.png",
            "input": f"inputs/{page_id}.json",
            "annotation": f"annotations/{page_id}.json",
        }
        for role, relative in expected_paths.items():
            record = page_record.get(role)
            if not isinstance(record, dict) or record.get("file") != relative:
                raise ContractError(f"manifest {page_id}: bad {role} path")
            path = root / relative
            if record.get("sha256") != sha256_path(path):
                raise ContractError(f"manifest {page_id}: {role} SHA mismatch")
        with Image.open(root / expected_paths["image"]) as image:
            if image.format != "PNG" or image.mode != "RGB" or image.size != (1440, 2048):
                raise ContractError(f"{page_id}: image must be single RGB 1440x2048 PNG")
        arm_input = load_arm_input(root / expected_paths["input"])
        gt = load_ground_truth(root / expected_paths["annotation"])
        if arm_input.page_id != page_id or gt.page_id != page_id:
            raise ContractError(f"{page_id}: page IDs disagree")
        input_ids = {region.region_id for region in arm_input.regions}
        gt_ids = set(gt.reading_order) | set(gt.unscored_region_ids)
        if input_ids != gt_ids:
            raise ContractError(f"{page_id}: arm-visible and scorer ID sets differ")
        a_count = sum("A" in pair.slices or "A+B" in pair.slices for pair in gt.qualification_pairs)
        b_count = sum("B" in pair.slices or "A+B" in pair.slices for pair in gt.qualification_pairs)
        a_pairs += a_count
        b_pairs += b_count
        if a_count:
            a_pages.add(page_id)
        if b_count:
            b_pages.add(page_id)
        clean_pages += int("clean-control" in gt.layout_tags)
        fallback_pages += int("intentional-fallback" in gt.layout_tags)
        open_pages += int("open-frame" in gt.layout_tags or "incomplete-frame" in gt.layout_tags)

    if a_pairs < 12 or len(a_pages) < 5:
        raise ContractError("held-out corpus does not meet frozen A pair/page minima")
    if b_pairs < 12 or len(b_pages) < 5:
        raise ContractError("held-out corpus does not meet frozen B pair/page minima")
    if clean_pages < 4 or fallback_pages < 2 or open_pages < 2:
        raise ContractError("held-out corpus does not meet frozen layout minima")

    inventory = manifest.get("inventory")
    if not isinstance(inventory, list):
        raise ContractError("manifest.inventory must be an array")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    declared = {item.get("file") for item in inventory if isinstance(item, dict)}
    if actual != declared:
        raise ContractError(
            "manifest inventory mismatch: "
            f"missing={sorted(actual-declared)}, extra={sorted(declared-actual)}"
        )
    for item in inventory:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            raise ContractError("malformed manifest inventory")
        path = root / item["file"]
        if item.get("bytes") != path.stat().st_size or item.get("sha256") != sha256_path(path):
            raise ContractError(f"inventory integrity mismatch: {item['file']}")


def main() -> None:
    validate_corpus()
    print("reading-order-v2 held-out corpus: valid")


if __name__ == "__main__":
    main()
