from __future__ import annotations

import copy
import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from scripts.public_benchmark.contracts import BenchmarkContractError, bind_observation
from scripts.public_benchmark.corpus import load_corpus
from scripts.public_benchmark.observation import load_observation

SHA40 = "1" * 40
SHA64 = "2" * 64


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


def _annotation(image_sha: str, *, raw: str = "字") -> dict[str, Any]:
    return {
        "schemaVersion": "1.0.0",
        "page": {
            "id": "msdemo-001-test",
            "imageSha256": image_sha,
            "width": 100,
            "height": 100,
            "split": "public-demo",
            "license": "CC-BY-4.0",
            "provenanceRef": "../manifest.json",
        },
        "regions": [
            {
                "id": "p1-r001",
                "geometry": {
                    "bbox": {"x": 10, "y": 10, "width": 20, "height": 20},
                    "polygon": [[10, 10], [30, 10], [30, 30], [10, 30]],
                },
                "transcription": {"raw": raw, "normalization": "strict-nfc-v1"},
                "orientation": "horizontal-ltr",
                "textRole": "dialogue",
                "textForm": "base",
                "readingOrder": {"position": 0, "scored": True},
                "difficultCaseTags": [],
                "scoring": {"detection": True, "recognition": True, "readingOrder": True},
            }
        ],
        "furiganaRelations": [],
        "presentationMarks": [],
        "negativeZones": [],
        "readingOrderContract": {"kind": "total", "sequence": ["p1-r001"]},
        "review": {},
    }


def _write_corpus(root: Path, *, raw: str = "字") -> None:
    (root / "annotations").mkdir(parents=True, exist_ok=True)
    (root / "images").mkdir(parents=True, exist_ok=True)
    schema = _json_bytes({"$schema": "https://json-schema.org/draft/2020-12/schema"})
    image = _png(100, 100)
    image_sha = _sha(image)
    annotation = _json_bytes(_annotation(image_sha, raw=raw))
    annotation_sha = _sha(annotation)
    schema_sha = _sha(schema)
    (root / "annotations" / "schema-v1.json").write_bytes(schema)
    (root / "images" / "page.png").write_bytes(image)
    (root / "annotations" / "page.json").write_bytes(annotation)
    manifest = {
        "schemaVersion": 1,
        "corpusId": "mangasensei-public-demo-v1",
        "pages": [
            {
                "id": "msdemo-001-test",
                "image": {
                    "file": "images/page.png",
                    "sha256": image_sha,
                    "width": 100,
                    "height": 100,
                },
                "annotation": {
                    "file": "annotations/page.json",
                    "sha256": annotation_sha,
                    "schema": "annotations/schema-v1.json",
                },
            }
        ],
        "inventory": [
            {"file": "annotations/schema-v1.json", "sha256": schema_sha},
            {"file": "images/page.png", "sha256": image_sha},
            {"file": "annotations/page.json", "sha256": annotation_sha},
        ],
    }
    (root / "manifest.json").write_bytes(_json_bytes(manifest))


def _observation(corpus_root: Path) -> dict[str, Any]:
    corpus = load_corpus(corpus_root)
    page = corpus.pages[0]
    return {
        "schemaVersion": "1.0.0",
        "kind": "mangasensei-public-ocr-observation",
        "corpus": {
            "id": corpus.corpus_id,
            "schemaVersion": corpus.schema_version,
            "manifestSha256": corpus.manifest_sha256,
            "annotationSchemaSha256": corpus.annotation_schema_sha256,
        },
        "producer": {
            "repositorySha": SHA40,
            "mangaSenseiVersion": "0.1.0",
            "sourceOcrContract": "mangasensei.ocr.contracts.OcrResult",
            "capturedAt": "2026-08-11T12:00:00Z",
        },
        "features": {
            "rawPolygon": True,
            "angle": True,
            "confidence": True,
            "readingOrder": True,
            "presentationMarks": False,
            "furiganaRelationships": False,
            "textRole": False,
            "linguistics": False,
        },
        "ocr": {
            "detector": "default",
            "recognizer": "48px",
            "modelManifestVersion": "2026-08-07",
            "modelManifestSha256": SHA64,
            "configDigestSha256": "3" * 64,
            "upstreamRepository": "https://github.com/zyddnys/manga-image-translator",
            "upstreamCommit": "4" * 40,
            "modelArtifacts": [],
        },
        "runtime": {
            "python": "3.11",
            "device": "cpu",
            "platform": "linux",
            "architecture": "x86_64",
        },
        "pages": [
            {
                "id": page.id,
                "imageSha256": page.image_sha256,
                "annotationSha256": page.annotation_sha256,
                "width": page.width,
                "height": page.height,
                "regions": [
                    {
                        "id": "obs-001",
                        "bbox": {"x": 10, "y": 10, "width": 20, "height": 20},
                        "polygon": [[10, 10], [30, 10], [30, 30]],
                        "angle": 0.0,
                        "confidence": 0.9,
                        "text": "字",
                        "readingOrder": 0,
                    }
                ],
            }
        ],
    }


def _write_observation(path: Path, value: dict[str, Any]) -> None:
    path.write_bytes(_json_bytes(value))


def test_valid_observation_binds_to_frozen_corpus(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    observation_path = tmp_path / "observation.json"
    _write_observation(observation_path, _observation(tmp_path))
    corpus = load_corpus(tmp_path)
    observation = load_observation(observation_path)
    bind_observation(corpus, observation)


def test_manifest_mismatch_fails_binding(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    value = _observation(tmp_path)
    corpus_value = value["corpus"]
    assert isinstance(corpus_value, dict)
    corpus_value["manifestSha256"] = "f" * 64
    path = tmp_path / "observation.json"
    _write_observation(path, value)
    with pytest.raises(BenchmarkContractError, match="manifestSha256"):
        bind_observation(load_corpus(tmp_path), load_observation(path))


@pytest.mark.parametrize("target", ["image", "annotation"])
def test_corpus_content_hash_mismatch_fails_closed(tmp_path: Path, target: str) -> None:
    _write_corpus(tmp_path)
    if target == "image":
        path = tmp_path / "images" / "page.png"
        path.write_bytes(path.read_bytes() + b"tamper")
    else:
        path = tmp_path / "annotations" / "page.json"
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(BenchmarkContractError, match=f"{target} SHA-256 mismatch"):
        load_corpus(tmp_path)


def test_duplicate_observation_ids_are_rejected(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    value = _observation(tmp_path)
    page = value["pages"][0]
    assert isinstance(page, dict)
    regions = page["regions"]
    assert isinstance(regions, list)
    duplicate = copy.deepcopy(regions[0])
    assert isinstance(duplicate, dict)
    duplicate["readingOrder"] = 1
    regions.append(duplicate)
    path = tmp_path / "observation.json"
    _write_observation(path, value)
    with pytest.raises(BenchmarkContractError, match="duplicate observation region id"):
        load_observation(path)


def test_invalid_geometry_and_confidence_are_rejected(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    for field, replacement, expected in (
        ("bbox", {"x": 95, "y": 10, "width": 20, "height": 20}, "outside page"),
        ("confidence", 1.1, "between 0 and 1"),
    ):
        value = _observation(tmp_path)
        page = value["pages"][0]
        assert isinstance(page, dict)
        regions = page["regions"]
        assert isinstance(regions, list)
        region = regions[0]
        assert isinstance(region, dict)
        region[field] = replacement
        path = tmp_path / f"{field}.json"
        _write_observation(path, value)
        with pytest.raises(BenchmarkContractError, match=expected):
            load_observation(path)


def test_ground_truth_non_nfc_and_three_point_polygon_are_rejected(tmp_path: Path) -> None:
    _write_corpus(tmp_path, raw="か\u3099")
    with pytest.raises(BenchmarkContractError, match="ground truth must already be NFC"):
        load_corpus(tmp_path)

    _write_corpus(tmp_path)
    annotation_path = tmp_path / "annotations" / "page.json"
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    annotation["regions"][0]["geometry"]["polygon"] = [[10, 10], [30, 10], [30, 30]]
    annotation_bytes = _json_bytes(annotation)
    annotation_path.write_bytes(annotation_bytes)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = _sha(annotation_bytes)
    manifest["pages"][0]["annotation"]["sha256"] = digest
    for entry in manifest["inventory"]:
        if entry["file"] == "annotations/page.json":
            entry["sha256"] = digest
    manifest_path.write_bytes(_json_bytes(manifest))
    with pytest.raises(BenchmarkContractError, match="at least 4 points"):
        load_corpus(tmp_path)


def test_missing_page_inventory_and_bad_reading_order_fail(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    value = _observation(tmp_path)
    value["pages"] = []
    path = tmp_path / "empty.json"
    _write_observation(path, value)
    with pytest.raises(BenchmarkContractError, match="must not be empty"):
        load_observation(path)

    value = _observation(tmp_path)
    page = value["pages"][0]
    assert isinstance(page, dict)
    regions = page["regions"]
    assert isinstance(regions, list)
    region = regions[0]
    assert isinstance(region, dict)
    region["readingOrder"] = 1
    _write_observation(path, value)
    with pytest.raises(BenchmarkContractError, match="contiguous from zero"):
        load_observation(path)


def test_observation_annotation_hash_mismatch_fails_binding(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    value = _observation(tmp_path)
    page = value["pages"][0]
    assert isinstance(page, dict)
    page["annotationSha256"] = "f" * 64
    path = tmp_path / "annotation-binding.json"
    _write_observation(path, value)
    with pytest.raises(BenchmarkContractError, match="annotationSha256"):
        bind_observation(load_corpus(tmp_path), load_observation(path))


def test_duplicate_ground_truth_region_ids_are_rejected(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    annotation_path = tmp_path / "annotations" / "page.json"
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    duplicate = copy.deepcopy(annotation["regions"][0])
    duplicate["readingOrder"] = {"position": None, "scored": False}
    duplicate["scoring"]["readingOrder"] = False
    annotation["regions"].append(duplicate)
    annotation_bytes = _json_bytes(annotation)
    annotation_path.write_bytes(annotation_bytes)

    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = _sha(annotation_bytes)
    manifest["pages"][0]["annotation"]["sha256"] = digest
    for entry in manifest["inventory"]:
        if entry["file"] == "annotations/page.json":
            entry["sha256"] = digest
    manifest_path.write_bytes(_json_bytes(manifest))

    with pytest.raises(BenchmarkContractError, match="duplicate region id"):
        load_corpus(tmp_path)


def test_manifest_paths_cannot_escape_corpus_root(tmp_path: Path) -> None:
    _write_corpus(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["pages"][0]["image"]["file"] = "../outside.png"
    manifest_path.write_bytes(_json_bytes(manifest))
    (tmp_path.parent / "outside.png").write_bytes(_png(100, 100))
    try:
        with pytest.raises(BenchmarkContractError, match="path escapes corpus root"):
            load_corpus(tmp_path)
    finally:
        (tmp_path.parent / "outside.png").unlink(missing_ok=True)
