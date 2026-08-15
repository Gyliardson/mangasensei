from __future__ import annotations

from scripts.public_benchmark.contracts import BBox
from scripts.public_benchmark.matching import assign_bbox_ids_one_to_one


def test_panel_matcher_exact_half_and_below_half() -> None:
    exact = assign_bbox_ids_one_to_one(
        (("g", BBox(0, 0, 10, 10)),),
        (("o", BBox(0, 0, 5, 10)),),
    )
    below = assign_bbox_ids_one_to_one(
        (("g", BBox(0, 0, 10, 10)),),
        (("o", BBox(0, 0, 4, 10)),),
    )
    assert [
        (item.ground_truth_id, item.observation_id, item.iou_ppm) for item in exact
    ] == [("g", "o", 500000)]
    assert below == ()


def test_panel_matcher_global_assignment_and_stable_tie() -> None:
    gt = (("g1", BBox(10, 0, 10, 10)), ("g2", BBox(13, 0, 10, 10)))
    obs = (("o1", BBox(10, 0, 10, 10)), ("o2", BBox(7, 0, 10, 10)))
    matches = assign_bbox_ids_one_to_one(gt, obs)
    assert [
        (item.ground_truth_id, item.observation_id) for item in matches
    ] == [("g1", "o2"), ("g2", "o1")]

    tied_gt = (("g2", BBox(0, 0, 10, 10)), ("g1", BBox(0, 0, 10, 10)))
    tied_obs = (("o2", BBox(0, 0, 10, 10)), ("o1", BBox(0, 0, 10, 10)))
    tied = assign_bbox_ids_one_to_one(tied_gt, tied_obs)
    assert [
        (item.ground_truth_id, item.observation_id) for item in tied
    ] == [("g1", "o1"), ("g2", "o2")]
