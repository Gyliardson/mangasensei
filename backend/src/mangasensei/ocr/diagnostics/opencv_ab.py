"""Reproducible comparison primitives for the OpenCV OCR migration experiment."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

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
                (
                    -overlap,
                    center_distance,
                    abs(math.log(area_ratio)),
                    baseline_index,
                    candidate_index,
                )
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
    selected = probability_plane[map_y1:map_y2, map_x1:map_x2].astype(
        np.float64, copy=False
    )
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


def write_fixture_artifact(
    root: Path,
    *,
    fixture_file: str,
    record: Mapping[str, object],
    arrays: Mapping[str, np.ndarray],
) -> dict[str, str]:
    """Write one controlled per-fixture record and its exact stage arrays."""
    fixture_path = _safe_fixture_path(fixture_file)
    fixture_root = root / "fixtures"
    fixture_root.mkdir(parents=True, exist_ok=True)
    label = "__".join(fixture_path.with_suffix("").parts)
    record_path = fixture_root / f"{label}.json"
    arrays_path = fixture_root / f"{label}.npz"
    payload = {**record, "file": fixture_file}
    record_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    np.savez_compressed(
        arrays_path,
        **{  # type: ignore[arg-type]
            name: np.ascontiguousarray(value) for name, value in arrays.items()
        },
    )
    return {
        "file": fixture_file,
        "record": record_path.relative_to(root).as_posix(),
        "arrays": arrays_path.relative_to(root).as_posix(),
    }


def write_probe_manifest(
    root: Path,
    *,
    metadata: Mapping[str, object],
    fixtures: Sequence[Mapping[str, str]],
) -> Path:
    """Write the environment/integrity envelope shared by every fixture probe."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "probe.json"
    payload = {**metadata, "fixtures": [dict(fixture) for fixture in fixtures]}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def write_fixture_notice(
    root: Path,
    *,
    source_url: str,
    work: str,
    author: str,
) -> Path:
    """Keep fixture attribution and handling constraints beside derived artifacts."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "FIXTURE_NOTICE.txt"
    path.write_text(
        "\n".join(
            (
                "Controlled OCR diagnostic artifacts",
                "",
                f"Work: {work}",
                f"Author: {author}",
                f"Terms and official source: {source_url}",
                "",
                "This directory may contain source-derived detector maps, recognizer crops,",
                "OCR transcripts, and geometry from the reviewed licensed fixture corpus.",
                "It must not be committed, published as an ordinary build artifact, or retained",
                "outside the repository's reviewed fixture/license policy.",
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def compare_probe_roots(baseline_root: Path, candidate_root: Path) -> dict[str, Any]:
    """Compare two same-code probes and retain detailed evidence in controlled JSON."""
    baseline_manifest = _read_json_object(baseline_root / "probe.json")
    candidate_manifest = _read_json_object(candidate_root / "probe.json")
    for key in (
        "schema_version",
        "repository_sha",
        "fixture_manifest_sha256",
        "model_manifest_sha256",
        "ocr_config_digest",
    ):
        if baseline_manifest.get(key) != candidate_manifest.get(key):
            raise ValueError(f"probe invariant differs: {key}")

    baseline_entries = _fixture_entries(baseline_manifest)
    candidate_entries = _fixture_entries(candidate_manifest)
    if set(baseline_entries) != set(candidate_entries):
        raise ValueError("probe fixture inventories differ")

    fixture_comparisons: list[dict[str, Any]] = []
    text_changes: list[dict[str, Any]] = []
    unmatched_detector_candidates = 0
    unmatched_final_regions = 0
    for fixture_file in sorted(baseline_entries):
        baseline_entry = baseline_entries[fixture_file]
        candidate_entry = candidate_entries[fixture_file]
        baseline_record = _read_json_object(baseline_root / baseline_entry["record"])
        candidate_record = _read_json_object(candidate_root / candidate_entry["record"])
        if baseline_record.get("image_sha256") != candidate_record.get("image_sha256"):
            raise ValueError(f"probe source image differs: {fixture_file}")

        baseline_arrays = _read_arrays(baseline_root / baseline_entry["arrays"])
        candidate_arrays = _read_arrays(candidate_root / candidate_entry["arrays"])
        detector_comparison = _compare_spatial_stage(
            _nested_records(baseline_record, "detector", "candidates"),
            _nested_records(candidate_record, "detector", "candidates"),
            score_key="score",
        )
        unmatched_detector_candidates += int(detector_comparison["unmatched_count"])

        final_comparison, fixture_text_changes = _compare_final_regions(
            fixture_file,
            _record_list(baseline_record, "final_regions"),
            _record_list(candidate_record, "final_regions"),
        )
        text_changes.extend(fixture_text_changes)
        unmatched_final_regions += int(final_comparison["unmatched_count"])

        crop_comparisons = _compare_array_backed_spatial_stage(
            _nested_records(baseline_record, "recognizer", "crops"),
            _nested_records(candidate_record, "recognizer", "crops"),
            baseline_arrays,
            candidate_arrays,
        )
        input_comparisons = _compare_array_backed_spatial_stage(
            _nested_records(baseline_record, "recognizer", "inputs"),
            _nested_records(candidate_record, "recognizer", "inputs"),
            baseline_arrays,
            candidate_arrays,
        )
        fixture_comparisons.append(
            {
                "file": fixture_file,
                "detector": detector_comparison,
                "final_regions": final_comparison,
                "recognizer_crops": crop_comparisons,
                "recognizer_inputs": input_comparisons,
                "arrays": _compare_named_array_stages(
                    baseline_record,
                    candidate_record,
                    baseline_arrays,
                    candidate_arrays,
                ),
            }
        )

    return {
        "schema_version": 1,
        "repository_sha": baseline_manifest["repository_sha"],
        "baseline_opencv": _opencv_distribution(baseline_manifest),
        "candidate_opencv": _opencv_distribution(candidate_manifest),
        "fixture_count": len(fixture_comparisons),
        "semantic_text_change_count": len(text_changes),
        "text_changes": text_changes,
        "unmatched_detector_candidates": unmatched_detector_candidates,
        "unmatched_final_regions": unmatched_final_regions,
        "fixtures": fixture_comparisons,
    }


def _compare_spatial_stage(
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
    *,
    score_key: str | None = None,
) -> dict[str, Any]:
    spatial = spatially_match(baseline, candidate)
    matches: list[dict[str, Any]] = []
    for match in spatial.matches:
        item: dict[str, Any] = {
            "baseline_index": match.baseline_index,
            "candidate_index": match.candidate_index,
            "intersection_over_union": match.intersection_over_union,
            "normalized_center_distance": match.normalized_center_distance,
            "baseline_bbox": list(_bbox(baseline[match.baseline_index])),
            "candidate_bbox": list(_bbox(candidate[match.candidate_index])),
        }
        if score_key is not None:
            baseline_score = _as_float(baseline[match.baseline_index][score_key])
            candidate_score = _as_float(candidate[match.candidate_index][score_key])
            item["score_delta"] = candidate_score - baseline_score
        matches.append(item)
    unmatched_count = len(spatial.unmatched_baseline) + len(spatial.unmatched_candidate)
    return {
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "matched_count": len(spatial.matches),
        "unmatched_count": unmatched_count,
        "unmatched_baseline": list(spatial.unmatched_baseline),
        "unmatched_candidate": list(spatial.unmatched_candidate),
        "matches": matches,
    }


def _compare_final_regions(
    fixture_file: str,
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    comparison = _compare_spatial_stage(baseline, candidate)
    text_changes: list[dict[str, Any]] = []
    for match in comparison["matches"]:
        baseline_index = int(match["baseline_index"])
        candidate_index = int(match["candidate_index"])
        baseline_region = baseline[baseline_index]
        candidate_region = candidate[candidate_index]
        baseline_text = str(baseline_region.get("text", ""))
        candidate_text = str(candidate_region.get("text", ""))
        text_equal = baseline_text == candidate_text
        match["text_equal"] = text_equal
        match["confidence_delta"] = _as_float(candidate_region["confidence"]) - _as_float(
            baseline_region["confidence"]
        )
        match["reading_order_equal"] = candidate_region.get(
            "reading_order"
        ) == baseline_region.get("reading_order")
        if not text_equal:
            text_changes.append(
                {
                    "file": fixture_file,
                    "baseline_index": baseline_index,
                    "candidate_index": candidate_index,
                    "baseline_text": baseline_text,
                    "candidate_text": candidate_text,
                }
            )
    comparison["text_change_count"] = len(text_changes)
    return comparison, text_changes


def _compare_array_backed_spatial_stage(
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
    baseline_arrays: Mapping[str, np.ndarray],
    candidate_arrays: Mapping[str, np.ndarray],
) -> list[dict[str, Any]]:
    spatial = spatially_match(baseline, candidate)
    comparisons: list[dict[str, Any]] = []
    for match in spatial.matches:
        baseline_record = baseline[match.baseline_index]
        candidate_record = candidate[match.candidate_index]
        baseline_key = str(baseline_record["array_key"])
        candidate_key = str(candidate_record["array_key"])
        comparisons.append(
            {
                "baseline_index": match.baseline_index,
                "candidate_index": match.candidate_index,
                "intersection_over_union": match.intersection_over_union,
                "delta": array_delta(
                    baseline_arrays[baseline_key], candidate_arrays[candidate_key]
                ),
            }
        )
    return comparisons


def _compare_named_array_stages(
    baseline_record: Mapping[str, Any],
    candidate_record: Mapping[str, Any],
    baseline_arrays: Mapping[str, np.ndarray],
    candidate_arrays: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    baseline_stages = _string_mapping(baseline_record.get("array_stages"))
    candidate_stages = _string_mapping(candidate_record.get("array_stages"))
    stage_names = sorted(set(baseline_stages) | set(candidate_stages))
    comparisons: dict[str, Any] = {}
    for stage_name in stage_names:
        baseline_key = baseline_stages.get(stage_name)
        candidate_key = candidate_stages.get(stage_name)
        if baseline_key is None or candidate_key is None:
            comparisons[stage_name] = {
                "present_in_both": False,
                "baseline_present": baseline_key is not None,
                "candidate_present": candidate_key is not None,
            }
            continue
        comparisons[stage_name] = {
            "present_in_both": True,
            **array_delta(baseline_arrays[baseline_key], candidate_arrays[candidate_key]),
        }
    return comparisons


def _safe_fixture_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("fixture path must be a safe relative POSIX path")
    return path


def _read_json_object(path: Path) -> dict[str, Any]:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object: {path}")
    return raw


def _read_arrays(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {name: np.asarray(archive[name]) for name in archive.files}


def _fixture_entries(manifest: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    raw_entries = manifest.get("fixtures")
    if not isinstance(raw_entries, list):
        raise ValueError("probe manifest fixtures must be a list")
    entries: dict[str, dict[str, str]] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("probe fixture entry must be an object")
        entry = {key: str(raw_entry[key]) for key in ("file", "record", "arrays")}
        _safe_fixture_path(entry["file"])
        if entry["file"] in entries:
            raise ValueError(f"duplicate probe fixture: {entry['file']}")
        entries[entry["file"]] = entry
    return entries


def _nested_records(
    record: Mapping[str, Any], parent_key: str, child_key: str
) -> list[Mapping[str, object]]:
    parent = record.get(parent_key)
    if not isinstance(parent, dict):
        raise ValueError(f"probe record {parent_key} must be an object")
    return _record_list(parent, child_key)


def _record_list(record: Mapping[str, Any], key: str) -> list[Mapping[str, object]]:
    value = record.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"probe record {key} must be a list of objects")
    return value


def _string_mapping(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("array_stages must be an object")
    return {str(key): str(item) for key, item in value.items()}


def _opencv_distribution(manifest: Mapping[str, Any]) -> str:
    opencv = manifest.get("opencv")
    if not isinstance(opencv, dict):
        raise ValueError("probe manifest opencv must be an object")
    return str(opencv["distribution_version"])


def _as_float(value: object) -> float:
    return float(cast(Any, value))


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
