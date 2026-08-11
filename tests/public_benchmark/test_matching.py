from __future__ import annotations

from scripts.public_benchmark.contracts import BBox, GroundTruthRegion, ObservedRegion
from scripts.public_benchmark.matching import (
    assign_one_to_one,
    eligible_at_half,
    intersection_union,
)


def gt(region_id: str, bbox: BBox) -> GroundTruthRegion:
    return GroundTruthRegion(
        id=region_id,
        bbox=bbox,
        polygon=(
            (bbox.x, bbox.y),
            (bbox.x + bbox.width, bbox.y),
            (bbox.x + bbox.width, bbox.y + bbox.height),
        ),
        transcription_raw="字",
        text_role="dialogue",
        text_form="base",
        detection_scored=True,
        recognition_scored=True,
        reading_order_scored=True,
        reading_order_position=0,
    )


def obs(region_id: str, bbox: BBox, order: int = 0) -> ObservedRegion:
    return ObservedRegion(
        id=region_id,
        bbox=bbox,
        polygon=None,
        angle=0.0,
        confidence=1.0,
        text="字",
        reading_order=order,
    )


def test_exact_iou_half_is_eligible_and_integer_decided() -> None:
    intersection, union = intersection_union(BBox(0, 0, 10, 10), BBox(0, 0, 5, 10))
    assert (intersection, union) == (50, 100)
    assert eligible_at_half(intersection, union)
    matches = assign_one_to_one((gt("g", BBox(0, 0, 10, 10)),), (obs("o", BBox(0, 0, 5, 10)),))
    assert [(match.ground_truth_id, match.observation_id, match.iou_ppm) for match in matches] == [
        ("g", "o", 500_000)
    ]


def test_partial_overlap_below_half_is_unmatched() -> None:
    matches = assign_one_to_one((gt("g", BBox(0, 0, 10, 10)),), (obs("o", BBox(0, 0, 4, 10)),))
    assert matches == ()


def test_global_assignment_maximizes_cardinality_before_iou() -> None:
    ground_truth = (
        gt("g1", BBox(10, 0, 10, 10)),
        gt("g2", BBox(13, 0, 10, 10)),
    )
    observations = (
        obs("o1", BBox(10, 0, 10, 10), 0),
        obs("o2", BBox(7, 0, 10, 10), 1),
    )
    matches = assign_one_to_one(ground_truth, observations)
    assert [(match.ground_truth_id, match.observation_id) for match in matches] == [
        ("g1", "o2"),
        ("g2", "o1"),
    ]


def test_equal_cost_tie_is_stable_by_sorted_ids_and_traversal() -> None:
    ground_truth = (gt("g2", BBox(0, 0, 10, 10)), gt("g1", BBox(0, 0, 10, 10)))
    observations = (obs("o2", BBox(0, 0, 10, 10), 1), obs("o1", BBox(0, 0, 10, 10), 0))
    first = assign_one_to_one(ground_truth, observations)
    second = assign_one_to_one(tuple(reversed(ground_truth)), tuple(reversed(observations)))
    expected = [("g1", "o1"), ("g2", "o2")]
    assert [(item.ground_truth_id, item.observation_id) for item in first] == expected
    assert [(item.ground_truth_id, item.observation_id) for item in second] == expected
