from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from shutil import which

import numpy as np
from PIL import Image

from mangasensei.ocr.diagnostics.reading_order_post_v2_calibration import (
    CalibrationConfig,
    CalibrationDiagnostic,
    run_post_v2_calibration_candidate,
)
from mangasensei.ocr.diagnostics.reading_order_v2 import run_reading_order_v2_arm
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId

from scripts.reading_order_v2.canonical import to_jsonable, write_canonical_json
from scripts.reading_order_v2.contracts import PAGE_IDS, arm_asset_paths, load_ground_truth
from scripts.reading_order_v2.fixtures import load_textblock_regions
from scripts.reading_order_v2.scoring import CorpusScore, score_corpus, score_page
from scripts.reading_order_v2.validate_corpus import CORPUS_ROOT, validate_corpus

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = REPO_ROOT / "var" / "research" / "reading-order-post-v2-calibration"

ARM_CONFIGS: dict[str, CalibrationConfig] = {
    "CONTROL": CalibrationConfig(),
    "C1_ONLY": CalibrationConfig(c1_boundary_guard=True),
    "C2_ONLY": CalibrationConfig(c2_uncertain_relations=True),
    "C1_C2": CalibrationConfig(c1_boundary_guard=True, c2_uncertain_relations=True),
    "C3_ONLY": CalibrationConfig(c3_merged_frame_recovery=True),
    "C1_C2_C3": CalibrationConfig(
        c1_boundary_guard=True,
        c2_uncertain_relations=True,
        c3_merged_frame_recovery=True,
    ),
    "B1_ONLY": CalibrationConfig(b1_local_order=True),
    "C1_C2_C3_B1": CalibrationConfig(
        c1_boundary_guard=True,
        c2_uncertain_relations=True,
        c3_merged_frame_recovery=True,
        b1_local_order=True,
    ),
}


def _git_head() -> str:
    git = which("git")
    if git is None:
        raise RuntimeError("git executable is required")
    result = subprocess.run(  # noqa: S603
        [git, "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _load_page(page_id: str):
    image_path, input_path = arm_asset_paths(CORPUS_ROOT, page_id)
    page, regions = load_textblock_regions(input_path)
    with Image.open(image_path) as opened:
        image = opened.convert("RGB")
        if image.size != (page.width, page.height):
            raise ValueError(f"{page_id}: image/input dimensions disagree")
        pixels = np.asarray(image)
    return page, regions, pixels


def _order_ids(diagnostic: CalibrationDiagnostic) -> tuple[str, ...]:
    return diagnostic.final_order


def _assert_frozen_v2_parity(
    *,
    page_id: str,
    repository_sha: str,
    page_height: int,
    pixels: object,
    regions: object,
    arm_name: str,
    candidate_order: tuple[str, ...],
) -> None:
    if arm_name == "CONTROL":
        frozen_arm = ArmId.A1_B0_PANEL_ONLY
    elif arm_name == "B1_ONLY":
        frozen_arm = ArmId.A1_B1_COMBINED
    else:
        return
    frozen = run_reading_order_v2_arm(
        pixels,
        regions,
        page_height=page_height,
        repository_sha=repository_sha,
        page_id=page_id,
        arm_id=frozen_arm,
    )
    frozen_order = tuple(item.region_id for item in frozen.ordered_regions)
    if candidate_order != frozen_order:
        raise AssertionError(
            f"{page_id}/{arm_name}: post-v2 baseline diverged from frozen {frozen_arm.value}: "
            f"candidate={candidate_order}, frozen={frozen_order}"
        )


def _score_record(score: CorpusScore) -> object:
    return to_jsonable(score)


def _assignment_map(diagnostic: CalibrationDiagnostic) -> dict[str, tuple[object, ...]]:
    return {
        item.region_id: (
            item.status,
            item.assigned_group_index,
            item.candidate_group_indices,
            item.reason,
        )
        for item in diagnostic.assignments
    }


def _segmentation_record(diagnostic: CalibrationDiagnostic) -> tuple[object, ...]:
    return (
        diagnostic.segmentation_reliable,
        diagnostic.segmentation_reason,
        diagnostic.recovery_reason,
        tuple((box.x1, box.y1, box.x2, box.y2) for box in diagnostic.segmentation_boxes),
    )


def _layout_tags(page_id: str) -> tuple[str, ...]:
    payload = json.loads(
        (CORPUS_ROOT / "annotations" / f"{page_id}.json").read_text(encoding="utf-8")
    )
    tags = payload.get("layoutTags", [])
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        raise ValueError(f"{page_id}: invalid layoutTags")
    return tuple(tags)


def _comparison(
    *,
    arm_scores: dict[str, CorpusScore],
    diagnostics: dict[str, dict[str, CalibrationDiagnostic]],
) -> dict[str, object]:
    control_score = arm_scores["CONTROL"]
    control_wrong = set(control_score.aggregate.wrong_pairs)
    control_pages = {page.page_id: page for page in control_score.pages}
    result: dict[str, object] = {}
    for arm_name in ARM_CONFIGS:
        score = arm_scores[arm_name]
        wrong = set(score.aggregate.wrong_pairs)
        page_changes: list[dict[str, object]] = []
        assignment_changes: list[dict[str, object]] = []
        segmentation_changes: list[dict[str, object]] = []
        clean_control_regressions: list[str] = []
        for page in score.pages:
            control_page = control_pages[page.page_id]
            control_diag = diagnostics["CONTROL"][page.page_id]
            candidate_diag = diagnostics[arm_name][page.page_id]
            if page.observed_scored_order != control_page.observed_scored_order:
                page_changes.append(
                    {
                        "pageId": page.page_id,
                        "control": control_page.observed_scored_order,
                        "candidate": page.observed_scored_order,
                    }
                )
            control_assignments = _assignment_map(control_diag)
            candidate_assignments = _assignment_map(candidate_diag)
            for region_id in sorted(set(control_assignments) | set(candidate_assignments)):
                before = control_assignments.get(region_id)
                after = candidate_assignments.get(region_id)
                if before != after:
                    assignment_changes.append(
                        {
                            "pageId": page.page_id,
                            "regionId": region_id,
                            "control": before,
                            "candidate": after,
                        }
                    )
            before_segmentation = _segmentation_record(control_diag)
            after_segmentation = _segmentation_record(candidate_diag)
            if before_segmentation != after_segmentation:
                segmentation_changes.append(
                    {
                        "pageId": page.page_id,
                        "control": before_segmentation,
                        "candidate": after_segmentation,
                    }
                )
            if (
                "clean-control" in _layout_tags(page.page_id)
                and control_page.exact_sequence
                and not page.exact_sequence
            ):
                clean_control_regressions.append(page.page_id)
        result[arm_name] = {
            "fixedWrongPairs": tuple(sorted(control_wrong - wrong)),
            "introducedWrongPairs": tuple(sorted(wrong - control_wrong)),
            "pageOrderChanges": tuple(page_changes),
            "assignmentChanges": tuple(assignment_changes),
            "segmentationChanges": tuple(segmentation_changes),
            "cleanControlRegressions": tuple(clean_control_regressions),
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run H01-H16 as observed CALIBRATION/REGRESSION fixtures only"
    )
    parser.add_argument("--repeat", type=int, choices=(1, 2, 3), required=True)
    args = parser.parse_args()

    validate_corpus(CORPUS_ROOT)
    repository_sha = _git_head()
    diagnostics: dict[str, dict[str, CalibrationDiagnostic]] = {
        arm_name: {} for arm_name in ARM_CONFIGS
    }
    arm_page_scores: dict[str, list[object]] = {arm_name: [] for arm_name in ARM_CONFIGS}

    for page_id in PAGE_IDS:
        page, regions, pixels = _load_page(page_id)
        ground_truth = load_ground_truth(CORPUS_ROOT / "annotations" / f"{page_id}.json")
        for arm_name, config in ARM_CONFIGS.items():
            result = run_post_v2_calibration_candidate(
                pixels,
                regions,
                page_height=page.height,
                config=config,
            )
            final_order = _order_ids(result.diagnostic)
            _assert_frozen_v2_parity(
                page_id=page_id,
                repository_sha=repository_sha,
                page_height=page.height,
                pixels=pixels,
                regions=regions,
                arm_name=arm_name,
                candidate_order=final_order,
            )
            diagnostics[arm_name][page_id] = result.diagnostic
            arm_page_scores[arm_name].append(score_page(ground_truth, final_order))

    arm_scores: dict[str, CorpusScore] = {
        arm_name: score_corpus(tuple(page_scores))
        for arm_name, page_scores in arm_page_scores.items()
    }
    payload = {
        "schemaVersion": "reading-order-post-v2-calibration-v1",
        "classification": "CALIBRATION_ONLY_OBSERVED_H01_H16_NOT_QUALIFICATION",
        "repositorySha": repository_sha,
        "arms": {
            arm_name: {
                "config": config,
                "score": _score_record(arm_scores[arm_name]),
                "pages": {
                    page_id: diagnostics[arm_name][page_id]
                    for page_id in PAGE_IDS
                },
            }
            for arm_name, config in ARM_CONFIGS.items()
        },
        "comparisonVsControl": _comparison(
            arm_scores=arm_scores,
            diagnostics=diagnostics,
        ),
    }
    output = OUTPUT_ROOT / f"repeat-{args.repeat}.json"
    write_canonical_json(output, payload)
    print(output.relative_to(REPO_ROOT))


if __name__ == "__main__":
    main()
