from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .contracts import BBox, GroundTruthRegion, ObservedRegion

IOU_PPM_SCALE = 1_000_000


@dataclass(frozen=True, slots=True)
class Match:
    ground_truth_id: str
    observation_id: str
    intersection_area: int
    union_area: int
    iou_ppm: int


def intersection_union(left: BBox, right: BBox) -> tuple[int, int]:
    x1 = max(left.x, right.x)
    y1 = max(left.y, right.y)
    x2 = min(left.x + left.width, right.x + right.width)
    y2 = min(left.y + left.height, right.y + right.height)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = left.area + right.area - intersection
    return intersection, union


def eligible_at_half(intersection_area: int, union_area: int) -> bool:
    return intersection_area > 0 and 2 * intersection_area >= union_area


def iou_ppm(intersection_area: int, union_area: int) -> int:
    if union_area <= 0:
        raise ValueError("union area must be positive")
    return intersection_area * IOU_PPM_SCALE // union_area


def observation_coverage_ppm(observation: BBox, target: BBox) -> tuple[int, int, int]:
    intersection, _ = intersection_union(observation, target)
    return intersection, observation.area, intersection * IOU_PPM_SCALE // observation.area


def _hungarian_minimize(costs: list[list[Fraction]]) -> list[int]:
    """Return columns selected by deterministic rectangular Hungarian minimization."""
    row_count = len(costs)
    if row_count == 0:
        return []
    column_count = len(costs[0])
    if column_count < row_count:
        raise ValueError("Hungarian matrix must have at least as many columns as rows")
    if any(len(row) != column_count for row in costs):
        raise ValueError("Hungarian matrix is ragged")

    u = [Fraction(0) for _ in range(row_count + 1)]
    v = [Fraction(0) for _ in range(column_count + 1)]
    p = [0 for _ in range(column_count + 1)]
    way = [0 for _ in range(column_count + 1)]
    for row_index in range(1, row_count + 1):
        p[0] = row_index
        current_column = 0
        min_values: list[Fraction | None] = [None for _ in range(column_count + 1)]
        used = [False for _ in range(column_count + 1)]
        while True:
            used[current_column] = True
            current_row = p[current_column]
            delta: Fraction | None = None
            next_column = 0
            for column_index in range(1, column_count + 1):
                if used[column_index]:
                    continue
                candidate = (
                    costs[current_row - 1][column_index - 1]
                    - u[current_row]
                    - v[column_index]
                )
                previous = min_values[column_index]
                if previous is None or candidate < previous:
                    min_values[column_index] = candidate
                    way[column_index] = current_column
                current_min = min_values[column_index]
                assert current_min is not None
                if delta is None or current_min < delta:
                    delta = current_min
                    next_column = column_index
            if delta is None:
                raise RuntimeError("Hungarian algorithm could not advance")
            for column_index in range(column_count + 1):
                if used[column_index]:
                    u[p[column_index]] += delta
                    v[column_index] -= delta
                elif column_index != 0:
                    current_min = min_values[column_index]
                    if current_min is not None:
                        min_values[column_index] = current_min - delta
            current_column = next_column
            if p[current_column] == 0:
                break
        while True:
            previous_column = way[current_column]
            p[current_column] = p[previous_column]
            current_column = previous_column
            if current_column == 0:
                break
    assignment = [-1 for _ in range(row_count)]
    for column_index in range(1, column_count + 1):
        assigned_row = p[column_index]
        if assigned_row != 0:
            assignment[assigned_row - 1] = column_index - 1
    if any(column < 0 for column in assignment):
        raise RuntimeError("Hungarian assignment is incomplete")
    return assignment


def assign_bbox_ids_one_to_one(
    ground_truth: tuple[tuple[str, BBox], ...],
    observations: tuple[tuple[str, BBox], ...],
) -> tuple[Match, ...]:
    """Match arbitrary named bboxes with the frozen public-benchmark semantics."""
    sorted_gt = tuple(sorted(ground_truth, key=lambda item: item[0]))
    sorted_observations = tuple(sorted(observations, key=lambda item: item[0]))
    if not sorted_gt or not sorted_observations:
        return ()

    cardinality_bonus = len(sorted_gt) + 1
    costs: list[list[Fraction]] = []
    geometry: dict[tuple[int, int], tuple[int, int]] = {}
    for gt_index, (_, gt_bbox) in enumerate(sorted_gt):
        row: list[Fraction] = []
        for obs_index, (_, observation_bbox) in enumerate(sorted_observations):
            intersection, union = intersection_union(gt_bbox, observation_bbox)
            geometry[(gt_index, obs_index)] = (intersection, union)
            if eligible_at_half(intersection, union):
                score = Fraction(cardinality_bonus, 1) + Fraction(intersection, union)
                row.append(-score)
            else:
                row.append(Fraction(1, 1))
        row.extend(Fraction(0, 1) for _ in sorted_gt)
        costs.append(row)

    selected_columns = _hungarian_minimize(costs)
    matches: list[Match] = []
    for gt_index, selected_column in enumerate(selected_columns):
        if selected_column >= len(sorted_observations):
            continue
        intersection, union = geometry[(gt_index, selected_column)]
        if not eligible_at_half(intersection, union):
            continue
        matches.append(
            Match(
                ground_truth_id=sorted_gt[gt_index][0],
                observation_id=sorted_observations[selected_column][0],
                intersection_area=intersection,
                union_area=union,
                iou_ppm=iou_ppm(intersection, union),
            )
        )
    return tuple(sorted(matches, key=lambda match: match.ground_truth_id))


def assign_one_to_one(
    ground_truth: tuple[GroundTruthRegion, ...],
    observations: tuple[ObservedRegion, ...],
) -> tuple[Match, ...]:
    """Globally maximize eligible match count, then aggregate exact IoU, with stable ties."""
    return assign_bbox_ids_one_to_one(
        tuple((region.id, region.bbox) for region in ground_truth),
        tuple((region.id, region.bbox) for region in observations),
    )
