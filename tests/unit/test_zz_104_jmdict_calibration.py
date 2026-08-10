from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_calibrate_reviewed_german_pack_metadata() -> None:
    root = Path(__file__).parents[2]
    completed = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(root / "scripts" / "update_jmdict_manifest.py"),
            "--language",
            "de",
            "--check",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
