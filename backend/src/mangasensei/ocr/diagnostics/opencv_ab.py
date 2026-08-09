"""Reproducible comparison primitives for the OpenCV OCR migration experiment."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

_DIAGNOSTIC_ROOT = Path("var") / "ocr-opencv-ab"


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
    pair_candidates: list[tuple[float, float, float, int, int]] = []
    for baseline_index, baseline_record in enumerate(baseline):
        baseline_box = _bbox(baseline_record)
        for candidate_index, candidate_record in enumerate(candidate):
            candidate_box = _bbox(candidate_record)
            overlap = _intersection_over_union(baseline_box, candidate_box)
            center_distance = _normalized_center_distance(baseline_box, candidate_box)
            area_ratio = _area_ratio(baseline_box, candidate_box)
            if overlap <= 0.0 and (center_distance > 1.0 or area_ratio > 4.0):
                continue
            pair_candidates.append(
                (-overlap, center_distance, abs(math.log(area_ratio)), baseline_index, candidate_index)
            )

    used_baseline: set[int] = set()
    used_candidate: set[int] = set()
    matches: list[SpatialMatch] = []
    for negative_overlap, center_distance, _, baseline_index, candidate_index in sorted(
        pair_candidates
    ):
        if baseline_index in used_baseline or candidate_index in used_candidate:
            continue
        used_baseline.add(baseline_index)
        used_candidate.add(candidate_index)
        matches.append(
            SpatialMatch(
                baseline_index=baseline_index,
                candidate_index=candidate_index,
                intersection_over_union=-negative_overlap,
                normalized_center_distance=center_distance,
            )
        )

    return SpatialMatchResult(
        matches=tuple(sorted(matches, key=lambda match: match.baseline_index)),
        unmatched_baseline=tuple(index for index in range(len(baseline)) if index not in used_baseline),
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


def validate_artifact_root(path: Path, *, repository_root: Path) -> Path:
    """Keep licensed/source-derived diagnostics under the repository's ignored root."""
    resolved_repository = repository_root.resolve()
    allowed_root = (resolved_repository / _DIAGNOSTIC_ROOT).resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(allowed_root):
        raise ValueError("diagnostic artifacts must stay under var/ocr-opencv-ab")
    return resolved_path


def safe_comparison_summary(comparison: Mapping[str, object]) -> str:
    """Render aggregate evidence without OCR text, pixels, logits, or secrets."""
    keys = (
        "baseline_opencv",
        "candidate_opencv",
        "fixture_count",
        "semantic_text_change_count",
        "unmatched_detector_candidates",
        "unmatched_final_regions",
    )
    return " ".join(f"{key}={comparison.get(key)!s}" for key in keys)


def _bbox(record: Mapping[str, object]) -> tuple[float, float, float, float]:
    raw = record.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise ValueError("spatial record bbox must contain four coordinates")
    x1, y1, x2, y2 = (float(value) for value in raw)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("spatial record bbox must have positive area")
    return x1, y1, x2, y2


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
