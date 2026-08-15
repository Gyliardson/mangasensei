from __future__ import annotations

from scripts.public_benchmark.contracts import BBox
from scripts.public_benchmark.matching import assign_bbox_ids_one_to_one
from scripts.reading_order_v2.contracts import AnnotationPage, PanelGroundTruth
from scripts.reading_order_v2.panel_scoring import DetectedPanel, score_panels


def annotation(
    panels: tuple[PanelGroundTruth, ...], precedence: tuple[tuple[str, str], ...] = ()
) -> AnnotationPage:
    return AnnotationPage(
        "H01", 200, 200, "0" * 64, (), (), (), (), panels, precedence
    )


def test_panel_iou_exactly_half_is_eligible() -> None:
    gt = (("g", BBox(0, 0, 10, 10)),)
    observed = (("o", BBox(0, 0, 5, 10)),)
    matches = assign_bbox_ids_one_to_one(gt, observed)
    assert [
        (match.ground_truth_id, match.observation_id, match.iou_ppm)
        for match in matches
    ] == [("g", "o", 500_000)]


def test_panel_iou_below_half_is_ineligible() -> None:
    assert assign_bbox_ids_one_to_one(
        (("g", BBox(0, 0, 10, 10)),),
        (("o", BBox(0, 0, 4, 10)),),
    ) == ()


def test_global_assignment_conflict_and_stable_tie_are_deterministic() -> None:
    gt = (("g2", BBox(13, 0, 10, 10)), ("g1", BBox(10, 0, 10, 10)))
    obs = (("o2", BBox(7, 0, 10, 10)), ("o1", BBox(10, 0, 10, 10)))
    matches = assign_bbox_ids_one_to_one(gt, obs)
    assert [(item.ground_truth_id, item.observation_id) for item in matches] == [
        ("g1", "o2"),
        ("g2", "o1"),
    ]
    duplicate = (("g2", BBox(0, 0, 10, 10)), ("g1", BBox(0, 0, 10, 10)))
    observations = (
        ("o2", BBox(0, 0, 10, 10)),
        ("o1", BBox(0, 0, 10, 10)),
    )
    first = assign_bbox_ids_one_to_one(duplicate, observations)
    second = assign_bbox_ids_one_to_one(
        tuple(reversed(duplicate)), tuple(reversed(observations))
    )
    assert [(item.ground_truth_id, item.observation_id) for item in first] == [
        ("g1", "o1"),
        ("g2", "o2"),
    ]
    assert first == second


def test_assignment_and_precedence_diagnostics_use_matched_panel_ids() -> None:
    gt = annotation(
        (
            PanelGroundTruth("p1", BBox(0, 0, 80, 80), True),
            PanelGroundTruth("p2", BBox(100, 0, 80, 80), True),
        ),
        (("p2", "p1"),),
    )
    score = score_panels(
        gt,
        (
            DetectedPanel("g0", BBox(0, 0, 80, 80)),
            DetectedPanel("g1", BBox(100, 0, 80, 80)),
        ),
        region_assignments={},
        detected_precedence=(("g0", "g1"),),
    )
    assert score.true_positive == 2
    assert score.precedence_comparable_count == 1
    assert score.precedence_inversions == 1
