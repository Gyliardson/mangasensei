from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
RETIRED_CORPUS_ID = "mangasensei-reading-order-post-v2-heldout-v1"
RETIRED_CORPUS_VERSION = "1.0.0"
RETIRED_MANIFEST_PATH = (
    REPO_ROOT / "assets" / "reading-order-post-v2" / "heldout-v1" / "manifest.json"
)


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _load_retired_manifest() -> dict[str, Any]:
    if not RETIRED_MANIFEST_PATH.is_file():
        raise FileNotFoundError("retired post-v2 held-out v1 manifest is missing")
    payload = _load_object(RETIRED_MANIFEST_PATH)
    if not isinstance(payload.get("inventory"), list):
        raise ValueError("retired post-v2 held-out v1 manifest is malformed")
    if payload.get("corpusId") != RETIRED_CORPUS_ID or payload.get("version") != RETIRED_CORPUS_VERSION:
        raise ValueError("retired post-v2 held-out v1 manifest identity changed")
    return payload


def _retired_content_sha256() -> frozenset[str]:
    payload = _load_retired_manifest()
    digests = {
        item.get("sha256")
        for item in payload["inventory"]
        if isinstance(item, dict) and isinstance(item.get("sha256"), str)
    }
    if not digests:
        raise ValueError("retired post-v2 held-out v1 manifest has no content hashes")
    return frozenset(digests)


def assert_no_retired_post_v2_v1_reuse(corpus_root: Path) -> None:
    design_path = corpus_root / "corpus-design.json"
    manifest_path = corpus_root / "manifest.json"
    if not design_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("future sealed corpus design/manifest is missing")

    design = _load_object(design_path)
    manifest = _load_object(manifest_path)
    if design.get("corpusId") == RETIRED_CORPUS_ID or manifest.get("corpusId") == RETIRED_CORPUS_ID:
        raise ValueError("retired post-v2 held-out v1 corpus identity reuse is forbidden")

    inventory = manifest.get("inventory")
    if not isinstance(inventory, list):
        raise ValueError("future corpus manifest must contain an inventory array")
    retired = _retired_content_sha256()
    reused = sorted(
        str(item["file"])
        for item in inventory
        if isinstance(item, dict)
        and isinstance(item.get("file"), str)
        and isinstance(item.get("sha256"), str)
        and item["sha256"] in retired
    )
    if reused:
        raise ValueError(
            "retired post-v2 held-out v1 content hash reuse is forbidden: "
            + ", ".join(reused)
        )
