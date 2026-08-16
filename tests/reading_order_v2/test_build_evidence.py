from __future__ import annotations

import pytest
from scripts.reading_order_v2.build_evidence import SOURCE_PATHS, _image_hashes
from scripts.reading_order_v2.contracts import PAGE_IDS


def _manifest() -> dict[str, object]:
    return {
        "inventory": [
            {
                "file": f"images/{page_id}.png",
                "sha256": f"{index:064x}",
            }
            for index, page_id in enumerate(PAGE_IDS, start=1)
        ]
    }


def test_evidence_source_manifest_captures_runtime_and_dependency_boundaries() -> None:
    assert (
        "backend/src/mangasensei/ocr/vendor/manga_image_translator/"
        "manga_translator/utils/textblock.py"
    ) in SOURCE_PATHS
    assert (
        "backend/src/mangasensei/ocr/vendor/manga_image_translator/"
        "manga_translator/utils/generic2.py"
    ) in SOURCE_PATHS
    assert "scripts/reading_order_v2/build_evidence.py" in SOURCE_PATHS
    assert "pyproject.toml" in SOURCE_PATHS
    assert "uv.lock" in SOURCE_PATHS


def test_image_hashes_requires_exact_heldout_page_inventory() -> None:
    hashes = _image_hashes(_manifest())
    assert tuple(hashes) == PAGE_IDS
    assert len(hashes) == 16

    incomplete = _manifest()
    inventory = incomplete["inventory"]
    assert isinstance(inventory, list)
    inventory.pop()
    with pytest.raises(ValueError, match="exactly H01-H16"):
        _image_hashes(incomplete)
