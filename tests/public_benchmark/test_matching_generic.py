from __future__ import annotations

from scripts.public_benchmark.contracts import BBox, GroundTruthRegion, ObservedRegion
from scripts.public_benchmark.matching import assign_bbox_ids_one_to_one, assign_one_to_one


def _gt(region_id: str, bbox: BBox) -> GroundTruthRegion:
    return GroundTruthRegion(
        region_id,
        bbox,
        ((0, 0), (1, 0), (1, 1)),
        "字",
        "dialogue",
        "base",
        True,
        True,
        True,
        0,
    )


def _obs(region_id: str, bbox: BBox) -> ObservedRegion:
    return ObservedRegion(region_id, bbox, None, 0.0, 1.0, "字", 0)


def test_generic_bbox_extraction_is_identical_to_existing_wrapper() -> None:
    ground_truth = (
        _gt("g2", BBox(13, 0, 10, 10)),
        _gt("g1", BBox(10, 0, 10, 10)),
    )
    observations = (
        _obs("o2", BBox(7, 0, 10, 10)),
        _obs("o1", BBox(10, 0, 10, 10)),
    )
    direct = assign_one_to_one(ground_truth, observations)
    generic = assign_bbox_ids_one_to_one(
        tuple((item.id, item.bbox) for item in ground_truth),
        tuple((item.id, item.bbox) for item in observations),
    )
    assert generic == direct
