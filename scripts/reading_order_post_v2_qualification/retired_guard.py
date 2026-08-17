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


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("inventory"), list):
        raise ValueError("retired post-v2 held-out v1 manifest is malformed")
    if payload.get("corpusId") != RETIRED_CORPUS_ID or payload.get("version") != RETIRED_CORPUS_VERSION:
        raise ValueError("retired post-v2 held-out v1 manifest identity changed")
    return payload


def _retired_content_sha256() -> frozenset[str]:
    if not RETIRED_MANIFEST_PATH.is_file():
        raise FileNotFoundError("retired post-v2 held-out v1 manifest is missing")
    payload = _load_manifest(RETIRED_MANIFEST_PATH)
    digests = {
        item.get("sha256")
        for item in payload["inventory"]
        if isinstance(item, dict) and isinstance(item.get("sha256"), str)
    }
    if not digests:
        raise ValueError("retired post-v2 held-out v1 manifest has no content hashes")
    return frozenset(digests)


def assert_no_retired_post_v2_v1_content_reuse(corpus_root: Path) -> None:
    manifest_path = corpus_root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("future sealed corpus manifest is missing")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    inventory = payload.get("inventory") if isinstance(payload, dict) else None
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
