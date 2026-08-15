from __future__ import annotations

import json
from pathlib import Path

from .contracts import validate_corpus_design

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_PATH = REPO_ROOT / "assets" / "reading-order-v2" / "heldout-v1" / "corpus-design.json"


def main() -> None:
    data = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("corpus-design.json must contain an object")
    validate_corpus_design(data)
    print("reading-order-v2 corpus design: valid")


if __name__ == "__main__":
    main()
