from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

from mangasensei.ocr.diagnostics.reading_order_v2 import run_reading_order_v2_arm
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId, diagnostic_to_dict

from .canonical import write_canonical_json
from .contracts import PAGE_IDS, arm_asset_paths
from .fixtures import load_textblock_regions

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "assets" / "reading-order-v2" / "heldout-v1"
RAW_ROOT = REPO_ROOT / "var" / "research" / "reading-order-v2" / "raw"


def execute_page(
    *, page_id: str, arm_id: ArmId, repository_sha: str, repeat: int
) -> tuple[Path, Path]:
    if page_id not in PAGE_IDS:
        raise ValueError("page-id must be one of frozen H01..H16")
    if repeat not in {1, 2, 3}:
        raise ValueError("repeat must be 1, 2, or 3")
    image_path, input_path = arm_asset_paths(CORPUS_ROOT, page_id)
    page, regions = load_textblock_regions(input_path)
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
        if image.size != (page.width, page.height):
            raise ValueError(f"{page_id}: image/input dimensions disagree")
        pixels = np.asarray(image)
    result = run_reading_order_v2_arm(
        pixels,
        regions,
        page_height=page.height,
        repository_sha=repository_sha,
        page_id=page_id,
        arm_id=arm_id,
    )
    output_root = RAW_ROOT / arm_id.value / f"repeat-{repeat}"
    diagnostic_path = output_root / f"{page_id}.diagnostic.json"
    ordering_path = output_root / f"{page_id}.ordering.json"
    write_canonical_json(diagnostic_path, diagnostic_to_dict(result.diagnostic))
    write_canonical_json(
        ordering_path,
        {
            "schemaVersion": "reading-order-v2-ordering-v1",
            "armId": arm_id.value,
            "pageId": page_id,
            "finalOrder": [item.region_id for item in result.ordered_regions],
        },
    )
    return diagnostic_path, ordering_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one frozen Reading Order v2 arm page without GT access"
    )
    parser.add_argument("--page-id", required=True, choices=PAGE_IDS)
    parser.add_argument("--arm", required=True, choices=[arm.value for arm in ArmId])
    parser.add_argument("--repository-sha", required=True)
    parser.add_argument("--repeat", required=True, type=int, choices=(1, 2, 3))
    args = parser.parse_args()
    execute_page(
        page_id=args.page_id,
        arm_id=ArmId(args.arm),
        repository_sha=args.repository_sha,
        repeat=args.repeat,
    )


if __name__ == "__main__":
    main()
