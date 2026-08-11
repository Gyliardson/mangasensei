from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.public_benchmark.corpus import load_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "assets" / "public-demo"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_evaluator_understands_frozen_public_demo_corpus_read_only() -> None:
    manifest_path = CORPUS_ROOT / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("repository public-demo corpus is not present in this local assembly")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(manifest, dict)
    manifest_pages = manifest["pages"]
    assert isinstance(manifest_pages, list)
    assert len(manifest_pages) == 4

    tracked_paths = [manifest_path, CORPUS_ROOT / "annotations" / "schema-v1.json"]
    for page_value in manifest_pages:
        assert isinstance(page_value, dict)
        image = page_value["image"]
        annotation = page_value["annotation"]
        assert isinstance(image, dict)
        assert isinstance(annotation, dict)
        tracked_paths.extend(
            [CORPUS_ROOT / str(image["file"]), CORPUS_ROOT / str(annotation["file"])]
        )
    before = {path: _sha(path) for path in tracked_paths}

    corpus = load_corpus(CORPUS_ROOT)

    assert [page.id for page in corpus.pages] == [str(page["id"]) for page in manifest_pages]
    manifest_by_id: dict[str, dict[str, Any]] = {}
    for page_value in manifest_pages:
        assert isinstance(page_value, dict)
        manifest_by_id[str(page_value["id"])] = page_value

    recognition_forms: set[str] = set()
    has_reading_order = False
    furigana_relations = 0
    presentation_marks = 0
    negative_zones = 0
    for page in corpus.pages:
        manifest_page = manifest_by_id[page.id]
        image = manifest_page["image"]
        annotation = manifest_page["annotation"]
        assert isinstance(image, dict)
        assert isinstance(annotation, dict)
        assert page.image_sha256 == image["sha256"]
        assert page.annotation_sha256 == annotation["sha256"]
        assert (page.width, page.height) == (image["width"], image["height"])

        for region in page.regions:
            if region.recognition_scored:
                recognition_forms.add(region.text_form)
            has_reading_order |= region.reading_order_scored
        ordered = sorted(
            (region for region in page.regions if region.reading_order_scored),
            key=lambda region: region.reading_order_position
            if region.reading_order_position is not None
            else -1,
        )
        assert page.reading_order_sequence == tuple(region.id for region in ordered)
        furigana_relations += len(page.furigana_relation_ids)
        presentation_marks += len(page.presentation_mark_ids)
        negative_zones += len(page.negative_zones)

    assert recognition_forms == {"base", "ruby"}
    assert has_reading_order
    assert furigana_relations > 0
    assert presentation_marks > 0
    assert negative_zones > 0
    assert {path: _sha(path) for path in tracked_paths} == before
