from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    CalibrationConfig,
    run_post_v2_calibration_candidate,
)
from mangasensei.ocr.reading_order import PanelBox, segment_panel_groups

from . import DIAGNOSTIC_SCHEMA_VERSION
from .canonical import write_canonical_json
from .contracts import ArmId, load_arm_input
from .fixtures import build_textblock_regions


def _box(box: PanelBox) -> dict[str, int]:
    return {"x1": box.x1, "y1": box.y1, "x2": box.x2, "y2": box.y2}


def _snapshot(regions: tuple[Any, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            id(ref.region),
            ref.region_id,
            ref.source_index,
            tuple(int(value) for value in ref.region.xyxy),
            str(ref.region.text),
            float(ref.region.prob),
            str(getattr(ref.region, "direction", "")),
        )
        for ref in regions
    )


def _config(arm: ArmId) -> CalibrationConfig:
    return CalibrationConfig(
        c1_boundary_guard=arm.c1,
        c2_uncertain_relations=arm.c2,
        c3_merged_frame_recovery=arm.c3,
        b1_local_order=arm.b1,
    )


def execute_page(
    *,
    corpus_root: Path,
    page_id: str,
    arm_id: ArmId,
    execution_sha: str,
    repeat: int,
    output_root: Path,
) -> tuple[Path, Path]:
    if repeat not in {1, 2, 3}:
        raise ValueError("repeat must be 1, 2, or 3")
    input_path = corpus_root / "inputs" / f"{page_id}.json"
    image_path = corpus_root / "images" / f"{page_id}.png"
    page = load_arm_input(input_path)
    if page.page_id != page_id:
        raise ValueError("page-id does not match frozen input identity")
    regions = build_textblock_regions(page)
    before = _snapshot(regions)

    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
        if image.size != (page.width, page.height):
            raise ValueError(f"{page_id}: image/input dimensions disagree")
        pixels = np.asarray(image)

    pre_segmentation = segment_panel_groups(pixels)
    result = run_post_v2_calibration_candidate(
        pixels,
        regions,
        page_height=page.height,
        config=_config(arm_id),
    )

    after = _snapshot(regions)
    if after != before:
        raise AssertionError(
            "candidate modified frozen input region geometry/content/confidence/direction"
        )
    if len(result.ordered_regions) != len(regions):
        raise AssertionError("candidate changed region count")
    if {id(ref.region) for ref in result.ordered_regions} != {id(ref.region) for ref in regions}:
        raise AssertionError("candidate changed region object identity set")
    if {ref.region_id for ref in result.ordered_regions} != {ref.region_id for ref in regions}:
        raise AssertionError("candidate changed stable region ID set")

    assignments: list[dict[str, object]] = []
    for region_index, assignment in enumerate(result.diagnostic.assignments):
        ref = regions[region_index]
        assignments.append(
            {
                "regionId": assignment.region_id,
                "sourceIndex": ref.source_index,
                "candidateGroupIndices": list(assignment.candidate_group_indices),
                "status": assignment.status,
                "reason": assignment.reason,
                "assignedGroupIndex": assignment.assigned_group_index,
                "uncertainNodeLabel": (
                    f"u{region_index:03d}" if assignment.assigned_group_index is None else None
                ),
            }
        )

    diagnostic = {
        "schemaVersion": DIAGNOSTIC_SCHEMA_VERSION,
        "experimentArm": arm_id.value,
        "executionSha": execution_sha,
        "pageId": page_id,
        "preSegmentation": {
            "reliable": pre_segmentation.reliable,
            "reason": pre_segmentation.reason,
            "boxCount": len(pre_segmentation.boxes),
            "boxes": [_box(box) for box in pre_segmentation.boxes],
        },
        "segmentation": {
            "reliable": result.diagnostic.segmentation_reliable,
            "reason": result.diagnostic.segmentation_reason,
            "boxes": [_box(box) for box in result.diagnostic.segmentation_boxes],
        },
        "recoveryReason": result.diagnostic.recovery_reason,
        "assignments": assignments,
        "relationEdges": [
            {
                "sourceNode": edge.source_node,
                "targetNode": edge.target_node,
                "rule": edge.rule,
            }
            for edge in result.diagnostic.relation_edges
        ],
        "nodeOrder": list(result.diagnostic.node_order),
        "fallbackReason": result.diagnostic.fallback_reason,
        "usedPanelEvidence": result.diagnostic.used_panel_evidence,
        "fallbackOrder": list(result.diagnostic.fallback_order),
        "finalOrder": list(result.diagnostic.final_order),
        "regionDirections": {
            ref.region_id: str(getattr(ref.region, "direction", ""))
            for ref in regions
        },
        "regionIntegrity": {
            "countPreserved": True,
            "objectIdentitySetPreserved": True,
            "contentConfidenceGeometryPreserved": True,
        },
    }
    ordering = {
        "schemaVersion": "reading-order-post-v2-ordering-v1",
        "experimentArm": arm_id.value,
        "executionSha": execution_sha,
        "pageId": page_id,
        "finalOrder": [ref.region_id for ref in result.ordered_regions],
    }
    arm_root = output_root / "raw" / arm_id.value / f"repeat-{repeat}"
    diagnostic_path = arm_root / f"{page_id}.diagnostic.json"
    ordering_path = arm_root / f"{page_id}.ordering.json"
    write_canonical_json(diagnostic_path, diagnostic)
    write_canonical_json(ordering_path, ordering)
    return diagnostic_path, ordering_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one frozen post-v2 arm page without GT access"
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--arm", choices=[arm.value for arm in ArmId], required=True)
    parser.add_argument("--execution-sha", required=True)
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    execute_page(
        corpus_root=args.corpus_root,
        page_id=args.page_id,
        arm_id=ArmId(args.arm),
        execution_sha=args.execution_sha,
        repeat=args.repeat,
        output_root=args.output_root,
    )


if __name__ == "__main__":
    main()
