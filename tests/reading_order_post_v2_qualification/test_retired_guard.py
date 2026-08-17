from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.reading_order_post_v2_qualification.retired_guard import (
    assert_no_retired_post_v2_v1_reuse,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RETIRED_ROOT = REPO_ROOT / "assets" / "reading-order-post-v2" / "heldout-v1"
RETIRED_Q001_IMAGE_SHA256 = "0a8532bb2b8ee006140fa3bad257bbdea076fd3bd071d1b3237dc317e22f6bb6"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_retired_v1_corpus_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="corpus identity reuse is forbidden"):
        assert_no_retired_post_v2_v1_reuse(RETIRED_ROOT)


def test_retired_v1_content_hash_is_rejected_under_new_identity(tmp_path: Path) -> None:
    _write_json(tmp_path / "corpus-design.json", {"corpusId": "new-corpus", "version": "1"})
    _write_json(
        tmp_path / "manifest.json",
        {
            "corpusId": "new-corpus",
            "version": "1",
            "inventory": [
                {"file": "images/new-page.png", "sha256": RETIRED_Q001_IMAGE_SHA256}
            ],
        },
    )

    with pytest.raises(ValueError, match="content hash reuse is forbidden"):
        assert_no_retired_post_v2_v1_reuse(tmp_path)


def test_new_content_hash_is_not_rejected(tmp_path: Path) -> None:
    _write_json(tmp_path / "corpus-design.json", {"corpusId": "new-corpus", "version": "1"})
    _write_json(
        tmp_path / "manifest.json",
        {
            "corpusId": "new-corpus",
            "version": "1",
            "inventory": [{"file": "images/new-page.png", "sha256": "0" * 64}],
        },
    )

    assert_no_retired_post_v2_v1_reuse(tmp_path)
