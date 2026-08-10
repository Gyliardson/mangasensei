"""Geometry and numerical comparison primitives for OpenCV OCR diagnostics."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import networkx as nx  # type: ignore[import-untyped]
import numpy as np

_MIN_SPATIAL_IOU = 0.2
_MAX_SPATIAL_AREA_RATIO = 4.0


@dataclass(frozen=True, slots=True)
class SpatialMatch:
    """One geometry-based match independent of source enumeration order."""

    baseline_index: int
    candidate_index: int
    intersection_over_union: float
    normalized_center_distance: float


@dataclass(frozen=True, slots=True)
class SpatialMatchResult:
    """Matched and unmatched record indices for one spatial stage."""

    matches: tuple[SpatialMatch, ...]
    unmatched_baseline: tuple[int, ...]
    unmatched_candidate: tuple[int, ...]


def spatially_match(
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
) -> SpatialMatchResult:
    """Match records by overlap and proximity, never by contour/list position."""
    baseline_boxes = tuple(record_bbox(record) for record in baseline)
    candidate_boxes = tuple(record_bbox(record) for record in candidate)
    baseline_signatures = tuple(_record_match_signature(record) for record in baseline)
    candidate_signatures = tuple(_record_match_signature(record) for record in candidate)
    baseline_keys = tuple(zip(baseline_boxes, baseline_signatures, strict=True))
    candidate_keys = tuple(zip(candidate_boxes, candidate_signatures, strict=True))
    if len(set(baseline_keys)) != len(baseline_keys) or len(set(candidate_keys)) != len(
        candidate_keys
    ):
        raise ValueError("ambiguous spatial matching has duplicate geometry")

    graph = nx.Graph()
    pair_evidence: dict[tuple[int, int], tuple[float, float]] = {}
    maximum_match_count = min(len(baseline), len(candidate))
    # The geometry score dominates globally; the digest only resolves exact-score ties.
    primary_scale = (maximum_match_count + 1) * (1 << 256)
    for baseline_index, baseline_box in enumerate(baseline_boxes):
        for candidate_index, candidate_box in enumerate(candidate_boxes):
            overlap = _intersection_over_union(baseline_box, candidate_box)
            center_distance = _normalized_center_distance(baseline_box, candidate_box)
            area_ratio = _area_ratio(baseline_box, candidate_box)
            if overlap < _MIN_SPATIAL_IOU or area_ratio > _MAX_SPATIAL_AREA_RATIO:
                continue
            pair_evidence[(baseline_index, candidate_index)] = (overlap, center_distance)
            primary_weight = (
                round(overlap * 1_000_000_000)
                - round(center_distance * 1_000_000)
                - round(abs(math.log(area_ratio)) * 1_000)
            )
            discriminator_equal = int(
                baseline_signatures[baseline_index] == candidate_signatures[candidate_index]
            )
            hierarchical_weight = primary_weight * (maximum_match_count + 1) + discriminator_equal
            graph.add_edge(
                ("baseline", baseline_index),
                ("candidate", candidate_index),
                weight=hierarchical_weight * primary_scale
                + _spatial_tie_breaker(
                    baseline_box,
                    candidate_box,
                    baseline_signatures[baseline_index],
                    candidate_signatures[candidate_index],
                ),
            )

    raw_matching = nx.algorithms.matching.max_weight_matching(
        graph, maxcardinality=True, weight="weight"
    )
    matches: list[SpatialMatch] = []
    for left, right in raw_matching:
        if left[0] == "candidate":
            left, right = right, left
        baseline_index = int(left[1])
        candidate_index = int(right[1])
        overlap, center_distance = pair_evidence[(baseline_index, candidate_index)]
        matches.append(
            SpatialMatch(
                baseline_index=baseline_index,
                candidate_index=candidate_index,
                intersection_over_union=overlap,
                normalized_center_distance=center_distance,
            )
        )
    used_baseline = {match.baseline_index for match in matches}
    used_candidate = {match.candidate_index for match in matches}
    return SpatialMatchResult(
        matches=tuple(sorted(matches, key=lambda match: match.baseline_index)),
        unmatched_baseline=tuple(
            index for index in range(len(baseline)) if index not in used_baseline
        ),
        unmatched_candidate=tuple(
            index for index in range(len(candidate)) if index not in used_candidate
        ),
    )


def array_fingerprint(array: np.ndarray) -> str:
    """Hash an array together with its interpretation, not only its raw bytes."""
    contiguous = np.ascontiguousarray(array)
    header = json.dumps(
        {"dtype": contiguous.dtype.str, "shape": contiguous.shape},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256()
    digest.update(header)
    digest.update(b"\0")
    digest.update(contiguous.tobytes())
    return digest.hexdigest()


def array_descriptor(array: np.ndarray) -> dict[str, object]:
    """Describe an exact stored array without placing its values in normal output."""
    contiguous = np.ascontiguousarray(array)
    value_count = int(contiguous.size)
    return {
        "shape": list(contiguous.shape),
        "dtype": contiguous.dtype.str,
        "fingerprint": array_fingerprint(contiguous),
        "minimum": float(contiguous.min()) if value_count else None,
        "maximum": float(contiguous.max()) if value_count else None,
        "mean": float(contiguous.astype(np.float64).mean()) if value_count else None,
    }


def source_zone_statistics(
    probability_map: np.ndarray,
    *,
    source_width: int,
    source_height: int,
    resize_ratio: float,
    detector_input_width: int,
    detector_input_height: int,
    source_zone: tuple[int, int, int, int],
) -> dict[str, object]:
    """Measure a reviewed source-space area in the detector's raw probability map."""
    if min(source_width, source_height, detector_input_width, detector_input_height) <= 0:
        raise ValueError("source and detector dimensions must be positive")
    if resize_ratio <= 0:
        raise ValueError("resize_ratio must be positive")
    probability_plane = np.asarray(probability_map)
    while probability_plane.ndim > 2 and probability_plane.shape[0] > 0:
        probability_plane = probability_plane[0]
    if probability_plane.ndim != 2:
        raise ValueError("probability map must have one two-dimensional plane")

    source_x1, source_y1, source_x2, source_y2 = source_zone
    source_x1 = max(0, min(source_x1, source_width - 1))
    source_y1 = max(0, min(source_y1, source_height - 1))
    source_x2 = max(source_x1 + 1, min(source_x2, source_width))
    source_y2 = max(source_y1 + 1, min(source_y2, source_height))
    map_height, map_width = probability_plane.shape
    map_scale_x = map_width / detector_input_width
    map_scale_y = map_height / detector_input_height
    map_x1 = max(0, min(math.floor(source_x1 * resize_ratio * map_scale_x), map_width - 1))
    map_y1 = max(0, min(math.floor(source_y1 * resize_ratio * map_scale_y), map_height - 1))
    map_x2 = max(map_x1 + 1, min(math.ceil(source_x2 * resize_ratio * map_scale_x), map_width))
    map_y2 = max(
        map_y1 + 1,
        min(math.ceil(source_y2 * resize_ratio * map_scale_y), map_height),
    )
    selected = probability_plane[map_y1:map_y2, map_x1:map_x2].astype(np.float64, copy=False)
    return {
        "source_zone": list(source_zone),
        "map_bounds": [map_x1, map_y1, map_x2, map_y2],
        "value_count": int(selected.size),
        "minimum": float(selected.min()),
        "maximum": float(selected.max()),
        "mean": float(selected.mean()),
        "percentile_05": float(np.quantile(selected, 0.05)),
        "percentile_50": float(np.quantile(selected, 0.50)),
        "percentile_95": float(np.quantile(selected, 0.95)),
    }


def array_delta(baseline: np.ndarray, candidate: np.ndarray) -> dict[str, object]:
    """Characterize numerical drift without defining pixel equality as OCR correctness."""
    if baseline.shape != candidate.shape:
        return {
            "shape_equal": False,
            "baseline_shape": list(baseline.shape),
            "candidate_shape": list(candidate.shape),
        }
    baseline_values = baseline.astype(np.float64, copy=False)
    candidate_values = candidate.astype(np.float64, copy=False)
    absolute_delta = np.abs(candidate_values - baseline_values)
    changed_values = int(np.count_nonzero(absolute_delta))
    value_count = int(absolute_delta.size)
    return {
        "shape_equal": True,
        "baseline_dtype": baseline.dtype.str,
        "candidate_dtype": candidate.dtype.str,
        "value_count": value_count,
        "changed_values": changed_values,
        "changed_fraction": changed_values / value_count if value_count else 0.0,
        "max_absolute_delta": float(absolute_delta.max()) if value_count else 0.0,
        "mean_absolute_delta": float(absolute_delta.mean()) if value_count else 0.0,
        "root_mean_square_delta": (
            float(np.sqrt(np.square(absolute_delta).mean())) if value_count else 0.0
        ),
    }


def record_bbox(record: Mapping[str, object]) -> tuple[float, float, float, float]:
    raw = record.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError("spatial record bbox must contain four coordinates")
    x1, y1, x2, y2 = (float(value) for value in raw)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("spatial record bbox must have positive area")
    return x1, y1, x2, y2


def _spatial_tie_breaker(
    baseline_box: tuple[float, float, float, float],
    candidate_box: tuple[float, float, float, float],
    baseline_signature: str,
    candidate_signature: str,
) -> int:
    geometry = json.dumps(
        (baseline_box, candidate_box, baseline_signature, candidate_signature),
        separators=(",", ":"),
        allow_nan=False,
    ).encode("ascii")
    return int.from_bytes(hashlib.sha256(geometry).digest(), "big")


def _record_match_signature(record: Mapping[str, object]) -> str:
    stable_context = {
        key: record[key] for key in ("points", "direction", "text_height", "width") if key in record
    }
    return json.dumps(stable_context, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _intersection_over_union(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    intersection_width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    intersection_height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    intersection = intersection_width * intersection_height
    union = _area(left) + _area(right) - intersection
    return intersection / union if union else 0.0


def _normalized_center_distance(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_center = ((left[0] + left[2]) / 2, (left[1] + left[3]) / 2)
    right_center = ((right[0] + right[2]) / 2, (right[1] + right[3]) / 2)
    scale = max(
        left[2] - left[0],
        left[3] - left[1],
        right[2] - right[0],
        right[3] - right[1],
        1.0,
    )
    return math.dist(left_center, right_center) / scale


def _area_ratio(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    left_area = _area(left)
    right_area = _area(right)
    return max(left_area, right_area) / min(left_area, right_area)


def _area(box: tuple[float, float, float, float]) -> float:
    return (box[2] - box[0]) * (box[3] - box[1])
