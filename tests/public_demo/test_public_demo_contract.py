from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.public_demo import validate as public_demo_validate

ROOT = Path(__file__).resolve().parents[2]


def test_frozen_public_demo_contract() -> None:
    subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "public_demo" / "validate.py")],
        cwd=ROOT,
        check=True,
    )


def test_renderer_fails_closed_on_fonts() -> None:
    renderer = (ROOT / "scripts" / "public_demo" / "render.mjs").read_text(encoding="utf-8")
    assert "font SHA-256 mismatch" in renderer
    assert "missing required font" in renderer
    assert "document.fonts.check" in renderer
    assert "MangaSensei Sans v1" in renderer
    assert "MangaSensei Serif v1" in renderer
    assert "playwright" in renderer


def test_ground_truth_authoring_has_no_ocr_dependency() -> None:
    scripts = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "scripts" / "public_demo").glob("*"))
        if path.is_file()
    ).lower()
    assert "mangasensei.ocr" not in scripts
    assert "ocr output" not in scripts


def test_non_nfc_transcription_is_rejected(tmp_path: Path) -> None:
    annotation_path = (
        ROOT / "assets" / "public-demo" / "annotations" / "msdemo-001-station.json"
    )
    schema_path = ROOT / "assets" / "public-demo" / "annotations" / "schema-v1.json"
    source_path = ROOT / "assets" / "public-demo" / "source" / "msdemo-001-station.svg"

    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    annotation["regions"][0]["transcription"]["raw"] = "か\u3099"
    mutated_path = tmp_path / "non-nfc-station.json"
    mutated_path.write_text(
        json.dumps(annotation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    source_svg = source_path.read_text(encoding="utf-8")
    with pytest.raises(AssertionError, match="NFC-normalized"):
        public_demo_validate.validate_annotation(mutated_path, source_svg, schema)
