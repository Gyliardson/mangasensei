from __future__ import annotations

import subprocess
import sys
from pathlib import Path

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
