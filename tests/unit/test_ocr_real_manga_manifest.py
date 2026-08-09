from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "ocr" / "real_manga" / "black_jack"
)
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"


def test_licensed_manga_manifest_matches_committed_fixture_inventory() -> None:
    manifest = _load_manifest()
    assert manifest["schemaVersion"] == 1

    fixtures = manifest["fixtures"]
    assert isinstance(fixtures, list)
    assert fixtures

    listed_paths = [Path(_fixture_value(entry, "file")) for entry in fixtures]
    assert len(listed_paths) == len(set(listed_paths))
    assert all(not path.is_absolute() and ".." not in path.parts for path in listed_paths)

    committed_paths = {
        path.relative_to(FIXTURE_ROOT).as_posix() for path in FIXTURE_ROOT.rglob("*.jpg")
    }
    assert {path.as_posix() for path in listed_paths} == committed_paths

    for entry, relative_path in zip(fixtures, listed_paths, strict=True):
        image_path = FIXTURE_ROOT / relative_path
        content = image_path.read_bytes()
        assert hashlib.sha256(content).hexdigest() == _fixture_value(entry, "sha256")

        with Image.open(image_path) as image:
            assert image.width == _fixture_int(entry, "width")
            assert image.height == _fixture_int(entry, "height")
            image.verify()


def _load_manifest() -> dict[str, Any]:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    return raw


def _fixture_value(entry: object, key: str) -> str:
    assert isinstance(entry, dict)
    value = entry[key]
    assert isinstance(value, str)
    return value


def _fixture_int(entry: object, key: str) -> int:
    assert isinstance(entry, dict)
    value = entry[key]
    assert isinstance(value, int)
    return value
