from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from scripts.public_benchmark.contracts import BBox
from scripts.public_benchmark.matching import Match, assign_bbox_ids_one_to_one

from .contracts import PageGroundTruth


@dataclass(frozen=True, slots=True)
class PanelScore:
    gt_count: int
    observed_count: int
    matches: tuple[Match, ...]
    precision: Fraction
    recall: Fraction
    f1: Fraction
    assignment_correct: int
    assignment_total: int
    precedence_comparable_pairs: int
    precedence_inversions: int


def _bbox_from_xyxy(values: list[int] | tuple[int, int, int, int]) -> BBox:
    x1, y1, x2, y2 = (int(value) for value in values)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("panel diagnostic bbox must have positive area")
    return BBox(x1, y1, x2 - x1, y2 - y1)


def score_panels(
    gt: PageGroundTruth,
    diagnostic: dict[str, object],
    *,
    expected_region_panels: dict[str, str] | None = None,
) -> PanelScore:
    raw_groups = diagnostic.get("groups")
    if not isinstance(raw_groups, list):
        raise ValueError("diagnostic.groups must be an array")
    observed: list[tuple[str, BBox]] = []
    for value in raw_groups:
        if not isinstance(value, dict) or not isinstance(value.get("groupId"), str):
            raise ValueError("malformed group diagnostic")
        bbox = value.get("bbox")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(isinstance(item, int) for item in bbox)
        ):
            raise ValueError("malformed group bbox")
        observed.append((value["groupId"], _bbox_from_xyxy(bbox)))
    gt_boxes = tuple((panel.panel_id, panel.bbox) for panel in gt.panels)
    matches = assign_bbox_ids_one_to_one(gt_boxes, tuple(observed))
    tp = len(matches)
    precision = Fraction(tp, len(observed)) if observed else Fraction(int(not gt_boxes), 1)
    recall = Fraction(tp, len(gt_boxes)) if gt_boxes else Fraction(int(not observed), 1)
    f1 = (
        (2 * precision * recall) / (precision + recall)
        if precision + recall
        else Fraction(0, 1)
    )

    observed_to_gt = {match.observation_id: match.ground_truth_id for match in matches}
    assignment_correct = 0
    assignment_total = 0
    if expected_region_panels:
        raw_regions = diagnostic.get("regions")
        if not isinstance(raw_regions, list):
            raise ValueError("diagnostic.regions must be an array")
        for region in raw_regions:
            if not isinstance(region, dict):
                continue
            region_id = region.get("regionId")
            assigned = region.get("assignedGroupId")
            if not isinstance(region_id, str) or region_id not in expected_region_panels:
                continue
            assignment_total += 1
            if (
                isinstance(assigned, str)
                and observed_to_gt.get(assigned) == expected_region_panels[region_id]
            ):
                assignment_correct += 1

    gt_positions = {
        panel.panel_id: panel.precedence_position
        for panel in gt.panels
        if panel.precedence_position is not None
    }
    observed_positions: dict[str, int] = {}
    for value in raw_groups:
        if not isinstance(value, dict):
            continue
        group_id = value.get("groupId")
        precedence = value.get("precedenceIndex")
        if isinstance(group_id, str) and isinstance(precedence, int):
            matched_gt = observed_to_gt.get(group_id)
            if matched_gt is not None:
                observed_positions[matched_gt] = precedence
    ordered_gt = sorted(gt_positions, key=lambda panel_id: int(gt_positions[panel_id]))
    comparable = 0
    inversions = 0
    for left_index, left in enumerate(ordered_gt):
        if left not in observed_positions:
            continue
        for right in ordered_gt[left_index + 1 :]:
            if right not in observed_positions:
                continue
            comparable += 1
            if observed_positions[left] > observed_positions[right]:
                inversions += 1
    return PanelScore(
        gt_count=len(gt_boxes),
        observed_count=len(observed),
        matches=matches,
        precision=precision,
        recall=recall,
        f1=f1,
        assignment_correct=assignment_correct,
        assignment_total=assignment_total,
        precedence_comparable_pairs=comparable,
        precedence_inversions=inversions,
    )
