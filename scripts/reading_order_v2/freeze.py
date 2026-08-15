from __future__ import annotations

import json
from pathlib import Path

from .canonical import sha256_path, write_canonical_json
from .contracts import CORPUS_ID, CORPUS_VERSION, PAGE_IDS, validate_corpus_design

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "assets" / "reading-order-v2" / "heldout-v1"


def freeze_manifest(root: Path = CORPUS_ROOT) -> dict[str, object]:
    design_path = root / "corpus-design.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    if not isinstance(design, dict):
        raise ValueError("corpus-design.json must be an object")
    validate_corpus_design(design)
    pages: list[dict[str, object]] = []
    for page_id in PAGE_IDS:
        records: dict[str, dict[str, object]] = {}
        for role, relative in {
            "source": f"source/{page_id}.svg",
            "image": f"images/{page_id}.png",
            "input": f"inputs/{page_id}.json",
            "annotation": f"annotations/{page_id}.json",
        }.items():
            path = root / relative
            if not path.is_file():
                raise FileNotFoundError(
                    f"cannot freeze before all H01-H16 assets exist: {relative}"
                )
            records[role] = {"file": relative, "sha256": sha256_path(path)}
        pages.append({"id": page_id, **records})
    required_common = [
        "LICENSE",
        "NOTICE.md",
        "README.md",
        "provenance/toolchain.json",
        "corpus-design.json",
    ]
    for relative in required_common:
        if not (root / relative).is_file():
            raise FileNotFoundError(f"missing required held-out corpus file: {relative}")
    inventory = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        inventory.append(
            {
                "file": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_path(path),
            }
        )
    return {
        "schemaVersion": "reading-order-v2-manifest-v1",
        "corpusId": CORPUS_ID,
        "version": CORPUS_VERSION,
        "designSha256": sha256_path(design_path),
        "pages": pages,
        "inventory": inventory,
    }


def main() -> None:
    manifest = freeze_manifest()
    write_canonical_json(CORPUS_ROOT / "manifest.json", manifest)
    print("wrote frozen held-out manifest; do not modify corpus without versioning/re-freezing")


if __name__ == "__main__":
    main()
