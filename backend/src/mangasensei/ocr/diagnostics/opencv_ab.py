"""Reproducible comparison orchestration for the OpenCV OCR migration."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from itertools import groupby
from pathlib import Path
from typing import Any, cast

import numpy as np

from . import opencv_artifacts as artifact_io
from .opencv_matching import (
    array_delta,
    array_fingerprint,
    record_bbox,
    spatially_match,
)

_SEMANTIC_EQUIVALENCE_COUNTS = (
    "recognizer_text_change_count",
    "merge_text_change_count",
    "semantic_text_change_count",
    "merge_angle_change_count",
    "final_angle_change_count",
    "final_bbox_change_count",
    "final_polygon_presence_change_count",
    "final_polygon_value_change_count",
    "final_reading_order_change_count",
    "final_id_change_count",
    "reviewed_map_zone_change_count",
    "unmatched_detector_candidates",
    "unmatched_recognizer_candidates",
    "unmatched_recognizer_crops",
    "unmatched_recognizer_inputs",
    "recognizer_inference_context_change_count",
    "unmatched_merge_regions",
    "unmatched_final_regions",
)


def safe_comparison_summary(comparison: Mapping[str, object]) -> str:
    """Render aggregate evidence without OCR text, pixels, logits, or secrets."""
    keys = (
        "baseline_opencv",
        "candidate_opencv",
        "repository_sha",
        "baseline_probe_manifest_sha256",
        "candidate_probe_manifest_sha256",
        "fixture_count",
        "recognizer_text_change_count",
        "merge_text_change_count",
        "semantic_text_change_count",
        "merge_angle_change_count",
        "final_angle_change_count",
        "final_bbox_change_count",
        "final_polygon_presence_change_count",
        "final_polygon_value_change_count",
        "reviewed_map_zone_change_count",
        "unmatched_detector_candidates",
        "unmatched_recognizer_candidates",
        "unmatched_recognizer_crops",
        "unmatched_recognizer_inputs",
        "recognizer_inference_context_change_count",
        "unmatched_merge_regions",
        "unmatched_final_regions",
    )
    return " ".join(f"{key}={comparison.get(key)!s}" for key in keys)


def semantic_equivalence_failures(comparison: Mapping[str, object]) -> dict[str, int]:
    """Return only aggregate semantic gate failures, never controlled content."""
    failures: dict[str, int] = {}
    for key in _SEMANTIC_EQUIVALENCE_COUNTS:
        value = comparison.get(key)
        if not isinstance(value, int):
            raise ValueError(f"comparison is missing integer aggregate: {key}")
        if value:
            failures[key] = value
    return failures


def compare_probe_roots(
    baseline_root: Path,
    candidate_root: Path,
    *,
    expected_fixture_manifest_sha256: str | None = None,
    expected_model_manifest_sha256: str | None = None,
    expected_repository_sha: str | None = None,
    expected_fixture_sha256: Mapping[str, str] | None = None,
    expected_model_files: Sequence[Mapping[str, object]] | None = None,
    expected_reviewed_map_zones: (Mapping[str, Mapping[str, Sequence[int]]] | None) = None,
    expected_source_files: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compare two same-code probes and retain detailed evidence in controlled JSON."""
    baseline_root = baseline_root.resolve()
    candidate_root = candidate_root.resolve()
    if baseline_root == candidate_root:
        raise ValueError("baseline and candidate probe roots must differ")
    baseline_manifest_path = artifact_io.resolve_probe_member(baseline_root, "probe.json")
    candidate_manifest_path = artifact_io.resolve_probe_member(candidate_root, "probe.json")
    baseline_manifest, baseline_manifest_sha256 = artifact_io.read_json_object_with_sha(
        baseline_manifest_path
    )
    candidate_manifest, candidate_manifest_sha256 = artifact_io.read_json_object_with_sha(
        candidate_manifest_path
    )
    _validate_probe_pair(
        baseline_manifest,
        candidate_manifest,
        expected_fixture_manifest_sha256=expected_fixture_manifest_sha256,
        expected_model_manifest_sha256=expected_model_manifest_sha256,
        expected_repository_sha=expected_repository_sha,
        expected_model_files=expected_model_files,
        expected_source_files=expected_source_files,
    )

    baseline_entries = artifact_io.fixture_entries(baseline_manifest)
    candidate_entries = artifact_io.fixture_entries(candidate_manifest)
    if set(baseline_entries) != set(candidate_entries):
        raise ValueError("probe fixture inventories differ")
    if expected_fixture_sha256 is not None and set(baseline_entries) != set(
        expected_fixture_sha256
    ):
        raise ValueError("probe fixture inventory differs from the reviewed manifest")

    fixture_comparisons: list[dict[str, Any]] = []
    recognizer_text_changes: list[dict[str, Any]] = []
    merge_text_changes: list[dict[str, Any]] = []
    text_changes: list[dict[str, Any]] = []
    unmatched_detector_candidates = 0
    unmatched_recognizer_candidates = 0
    unmatched_recognizer_crops = 0
    unmatched_recognizer_inputs = 0
    recognizer_inference_context_change_count = 0
    unmatched_merge_regions = 0
    unmatched_final_regions = 0
    merge_angle_change_count = 0
    final_angle_change_count = 0
    final_bbox_change_count = 0
    final_polygon_presence_change_count = 0
    final_polygon_value_change_count = 0
    final_reading_order_change_count = 0
    final_id_change_count = 0
    reviewed_map_zone_change_count = 0
    for fixture_file in sorted(baseline_entries):
        baseline_entry = baseline_entries[fixture_file]
        candidate_entry = candidate_entries[fixture_file]
        baseline_record, baseline_arrays = _load_fixture_evidence(baseline_root, baseline_entry)
        candidate_record, candidate_arrays = _load_fixture_evidence(candidate_root, candidate_entry)
        if baseline_record.get("image_sha256") != candidate_record.get("image_sha256"):
            raise ValueError(f"probe source image differs: {fixture_file}")
        if (
            expected_fixture_sha256 is not None
            and baseline_record.get("image_sha256") != expected_fixture_sha256[fixture_file]
        ):
            raise ValueError(f"probe source image is not the reviewed fixture: {fixture_file}")

        detector_comparison = _compare_spatial_stage(
            _nested_records(baseline_record, "detector", "candidates"),
            _nested_records(candidate_record, "detector", "candidates"),
            score_key="score",
        )
        unmatched_detector_candidates += int(detector_comparison["unmatched_count"])
        recognizer_comparison, fixture_recognizer_changes = _compare_text_stage(
            fixture_file,
            "recognizer",
            _nested_records(baseline_record, "recognizer", "accepted"),
            _nested_records(candidate_record, "recognizer", "accepted"),
        )
        recognizer_text_changes.extend(fixture_recognizer_changes)
        unmatched_recognizer_candidates += int(recognizer_comparison["unmatched_count"])
        merge_comparison, fixture_merge_changes = _compare_text_stage(
            fixture_file,
            "merge",
            _nested_records(baseline_record, "merge", "regions"),
            _nested_records(candidate_record, "merge", "regions"),
        )
        merge_text_changes.extend(fixture_merge_changes)
        unmatched_merge_regions += int(merge_comparison["unmatched_count"])
        merge_angle_change_count += int(merge_comparison["angle_change_count"])
        final_comparison, fixture_text_changes = _compare_final_regions(
            fixture_file,
            _record_list(baseline_record, "final_regions"),
            _record_list(candidate_record, "final_regions"),
        )
        text_changes.extend(fixture_text_changes)
        unmatched_final_regions += int(final_comparison["unmatched_count"])
        final_angle_change_count += int(final_comparison["angle_change_count"])
        final_bbox_change_count += int(final_comparison["bbox_change_count"])
        final_polygon_presence_change_count += int(
            final_comparison["polygon_presence_change_count"]
        )
        final_polygon_value_change_count += int(final_comparison["polygon_value_change_count"])
        final_reading_order_change_count += int(final_comparison["reading_order_change_count"])
        final_id_change_count += int(final_comparison["id_change_count"])
        baseline_map_zones = _reviewed_map_zones(baseline_record)
        candidate_map_zones = _reviewed_map_zones(candidate_record)
        if expected_reviewed_map_zones is not None and fixture_file in expected_reviewed_map_zones:
            _validate_reviewed_map_zones(
                fixture_file,
                baseline_map_zones,
                expected_reviewed_map_zones[fixture_file],
            )
            _validate_reviewed_map_zones(
                fixture_file,
                candidate_map_zones,
                expected_reviewed_map_zones[fixture_file],
            )
        reviewed_map_zones_equal = baseline_map_zones == candidate_map_zones
        reviewed_map_zone_change_count += int(not reviewed_map_zones_equal)

        baseline_inputs = _nested_records(baseline_record, "recognizer", "inputs")
        candidate_inputs = _nested_records(candidate_record, "recognizer", "inputs")
        crop_comparisons = _compare_recognizer_arrays(
            fixture_file,
            "crop",
            _crops_with_input_shape(
                _nested_records(baseline_record, "recognizer", "crops"),
                baseline_inputs,
            ),
            _crops_with_input_shape(
                _nested_records(candidate_record, "recognizer", "crops"),
                candidate_inputs,
            ),
            baseline_arrays,
            candidate_arrays,
        )
        input_comparisons = _compare_recognizer_arrays(
            fixture_file,
            "input",
            baseline_inputs,
            candidate_inputs,
            baseline_arrays,
            candidate_arrays,
        )
        unmatched_recognizer_crops += int(crop_comparisons["unmatched_count"])
        unmatched_recognizer_inputs += int(input_comparisons["unmatched_count"])
        recognizer_inference_context_change_count += int(
            input_comparisons["inference_context_change_count"]
        )
        fixture_comparisons.append(
            {
                "file": fixture_file,
                "detector": detector_comparison,
                "recognizer_accepted": recognizer_comparison,
                "merge": merge_comparison,
                "final_regions": final_comparison,
                "reviewed_map_zones_equal": reviewed_map_zones_equal,
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
        "probe_schema_version": baseline_manifest["schema_version"],
        "repository_sha": baseline_manifest["repository_sha"],
        "baseline_opencv": _opencv_distribution(baseline_manifest),
        "candidate_opencv": _opencv_distribution(candidate_manifest),
        "baseline_probe_manifest_sha256": baseline_manifest_sha256,
        "candidate_probe_manifest_sha256": candidate_manifest_sha256,
        "fixture_count": len(fixture_comparisons),
        "recognizer_text_change_count": len(recognizer_text_changes),
        "recognizer_text_changes": recognizer_text_changes,
        "merge_text_change_count": len(merge_text_changes),
        "merge_text_changes": merge_text_changes,
        "semantic_text_change_count": len(text_changes),
        "text_changes": text_changes,
        "merge_angle_change_count": merge_angle_change_count,
        "final_angle_change_count": final_angle_change_count,
        "final_bbox_change_count": final_bbox_change_count,
        "final_polygon_presence_change_count": final_polygon_presence_change_count,
        "final_polygon_value_change_count": final_polygon_value_change_count,
        "final_reading_order_change_count": final_reading_order_change_count,
        "final_id_change_count": final_id_change_count,
        "reviewed_map_zone_change_count": reviewed_map_zone_change_count,
        "unmatched_detector_candidates": unmatched_detector_candidates,
        "unmatched_recognizer_candidates": unmatched_recognizer_candidates,
        "unmatched_recognizer_crops": unmatched_recognizer_crops,
        "unmatched_recognizer_inputs": unmatched_recognizer_inputs,
        "recognizer_inference_context_change_count": (recognizer_inference_context_change_count),
        "unmatched_merge_regions": unmatched_merge_regions,
        "unmatched_final_regions": unmatched_final_regions,
        "fixtures": fixture_comparisons,
    }


def _validate_probe_pair(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    expected_fixture_manifest_sha256: str | None,
    expected_model_manifest_sha256: str | None,
    expected_repository_sha: str | None,
    expected_model_files: Sequence[Mapping[str, object]] | None,
    expected_source_files: Mapping[str, str] | None,
) -> None:
    artifact_io.validate_probe_manifest(baseline)
    artifact_io.validate_probe_manifest(candidate)
    for manifest in (baseline, candidate):
        if (
            expected_fixture_manifest_sha256 is not None
            and manifest["fixture_manifest_sha256"] != expected_fixture_manifest_sha256
        ):
            raise ValueError("probe fixture manifest is not the reviewed repository manifest")
        if (
            expected_model_manifest_sha256 is not None
            and manifest["model_manifest_sha256"] != expected_model_manifest_sha256
        ):
            raise ValueError("probe model manifest is not the reviewed repository manifest")
        if (
            int(manifest["schema_version"]) == 2
            and expected_model_files is not None
            and artifact_io.normalized_model_files(manifest["model_files"])
            != artifact_io.normalized_model_files(expected_model_files)
        ):
            raise ValueError("probe loaded model files differ from the reviewed manifest")
        if (
            int(manifest["schema_version"]) == 2
            and expected_source_files is not None
            and manifest["source_files"] != expected_source_files
        ):
            raise ValueError("probe source files differ from the current clean checkout")
    for key in (
        "schema_version",
        "repository_sha",
        "fixture_manifest_sha256",
        "model_manifest_sha256",
        "ocr_config_digest",
    ):
        if baseline.get(key) != candidate.get(key):
            raise ValueError(f"probe invariant differs: {key}")
    if baseline.get("runtime") != candidate.get("runtime"):
        raise ValueError("probe invariant differs: runtime")
    if baseline.get("source_files") != candidate.get("source_files"):
        raise ValueError("probe invariant differs: source_files")
    if (
        expected_repository_sha is not None
        and baseline["repository_sha"] != expected_repository_sha
    ):
        raise ValueError("probe repository SHA differs from the current clean checkout")
    if baseline.get("model_files") != candidate.get("model_files"):
        raise ValueError("probe invariant differs: model_files")
    if _opencv_distribution(baseline) == _opencv_distribution(candidate):
        raise ValueError("OpenCV A/B probes must use different distribution versions")
    for identity_key in ("runtime_version", "build_information_sha256"):
        if _opencv_identity(baseline, identity_key) == _opencv_identity(candidate, identity_key):
            raise ValueError(f"OpenCV A/B probes use the same loaded {identity_key}")
    if int(baseline["schema_version"]) == 2 and _opencv_identity(
        baseline, "binary_sha256"
    ) == _opencv_identity(candidate, "binary_sha256"):
        raise ValueError("OpenCV A/B probes use the same loaded extension binary")
    if _opencv_runtime_configuration(baseline) != _opencv_runtime_configuration(candidate):
        raise ValueError("probe invariant differs: OpenCV runtime configuration")


def _load_fixture_evidence(
    root: Path, entry: Mapping[str, str]
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    record_path = artifact_io.resolve_probe_member(root, entry["record"])
    arrays_path = artifact_io.resolve_probe_member(root, entry["arrays"])
    return (
        artifact_io.read_json_object(record_path, entry.get("record_sha256")),
        artifact_io.read_arrays(arrays_path, entry.get("arrays_sha256")),
    )


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
            "baseline_bbox": list(record_bbox(baseline[match.baseline_index])),
            "candidate_bbox": list(record_bbox(candidate[match.candidate_index])),
        }
        if score_key is not None:
            baseline_score = _as_float(baseline[match.baseline_index][score_key])
            candidate_score = _as_float(candidate[match.candidate_index][score_key])
            item["score_delta"] = candidate_score - baseline_score
        geometry: dict[str, Any] = {}
        for key in ("points", "lines", "polygon"):
            baseline_geometry = baseline[match.baseline_index].get(key)
            candidate_geometry = candidate[match.candidate_index].get(key)
            if isinstance(baseline_geometry, (list, tuple)) and isinstance(
                candidate_geometry, (list, tuple)
            ):
                geometry[key] = array_delta(
                    np.asarray(baseline_geometry), np.asarray(candidate_geometry)
                )
        if geometry:
            item["geometry"] = geometry
        matches.append(item)
    return {
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "matched_count": len(spatial.matches),
        "unmatched_count": len(spatial.unmatched_baseline) + len(spatial.unmatched_candidate),
        "unmatched_baseline": list(spatial.unmatched_baseline),
        "unmatched_candidate": list(spatial.unmatched_candidate),
        "matches": matches,
    }


def _compare_final_regions(
    fixture_file: str,
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    comparison, text_changes = _compare_text_stage(fixture_file, "final", baseline, candidate)
    reading_order_change_count = 0
    id_change_count = 0
    bbox_change_count = 0
    polygon_presence_change_count = 0
    polygon_value_change_count = 0
    for match in comparison["matches"]:
        baseline_index = int(match["baseline_index"])
        candidate_index = int(match["candidate_index"])
        baseline_region = baseline[baseline_index]
        candidate_region = candidate[candidate_index]
        reading_order_equal = candidate_region.get("reading_order") == baseline_region.get(
            "reading_order"
        )
        match["reading_order_equal"] = reading_order_equal
        reading_order_change_count += int(not reading_order_equal)
        id_equal = candidate_region.get("id") == baseline_region.get("id")
        match["id_equal"] = id_equal
        id_change_count += int(not id_equal)
        bbox_delta = array_delta(
            np.asarray(record_bbox(baseline_region)),
            np.asarray(record_bbox(candidate_region)),
        )
        match["bbox_delta"] = bbox_delta
        bbox_change_count += int(
            not bool(bbox_delta.get("shape_equal"))
            or _as_int(bbox_delta.get("changed_values", 0)) > 0
        )
        baseline_polygon_present = isinstance(baseline_region.get("polygon"), (list, tuple))
        candidate_polygon_present = isinstance(candidate_region.get("polygon"), (list, tuple))
        polygon_presence_equal = baseline_polygon_present == candidate_polygon_present
        match["polygon_presence_equal"] = polygon_presence_equal
        polygon_presence_change_count += int(not polygon_presence_equal)
        polygon_delta = cast(dict[str, Any], match.get("geometry", {})).get("polygon")
        if isinstance(polygon_delta, dict):
            polygon_value_change_count += int(
                not bool(polygon_delta.get("shape_equal"))
                or _as_int(polygon_delta.get("changed_values", 0)) > 0
            )
    comparison["reading_order_change_count"] = reading_order_change_count
    comparison["id_change_count"] = id_change_count
    comparison["bbox_change_count"] = bbox_change_count
    comparison["polygon_presence_change_count"] = polygon_presence_change_count
    comparison["polygon_value_change_count"] = polygon_value_change_count
    return comparison, text_changes


def _compare_text_stage(
    fixture_file: str,
    stage: str,
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    comparison = _compare_spatial_stage(baseline, candidate)
    text_changes: list[dict[str, Any]] = []
    angle_change_count = 0
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
        if "angle" in baseline_region or "angle" in candidate_region:
            baseline_angle = baseline_region.get("angle")
            candidate_angle = candidate_region.get("angle")
            angle_equal = baseline_angle == candidate_angle
            match["angle_equal"] = angle_equal
            angle_change_count += int(not angle_equal)
            if baseline_angle is not None and candidate_angle is not None:
                match["angle_delta"] = _as_float(candidate_angle) - _as_float(baseline_angle)
        if not text_equal:
            text_changes.append(
                {
                    "file": fixture_file,
                    "stage": stage,
                    "baseline_index": baseline_index,
                    "candidate_index": candidate_index,
                    "baseline_text_sha256": hashlib.sha256(
                        baseline_text.encode("utf-8")
                    ).hexdigest(),
                    "candidate_text_sha256": hashlib.sha256(
                        candidate_text.encode("utf-8")
                    ).hexdigest(),
                }
            )
    comparison["text_change_count"] = len(text_changes)
    comparison["angle_change_count"] = angle_change_count
    return comparison, text_changes


def _compare_recognizer_arrays(
    fixture_file: str,
    stage: str,
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
    baseline_arrays: Mapping[str, np.ndarray],
    candidate_arrays: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    try:
        return _compare_array_backed_spatial_stage(
            baseline, candidate, baseline_arrays, candidate_arrays
        )
    except ValueError as error:
        if not any(
            marker in str(error)
            for marker in ("ambiguous spatial matching", "duplicate array geometry")
        ):
            raise
        raise ValueError(
            f"ambiguous recognizer {stage} matching for fixture: {fixture_file}"
        ) from error


def _compare_array_backed_spatial_stage(
    baseline: Sequence[Mapping[str, object]],
    candidate: Sequence[Mapping[str, object]],
    baseline_arrays: Mapping[str, np.ndarray],
    candidate_arrays: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    baseline_groups = _group_array_records(baseline, baseline_arrays)
    candidate_groups = _group_array_records(candidate, candidate_arrays)
    spatial = spatially_match(
        tuple(group[0] for group in baseline_groups),
        tuple(group[0] for group in candidate_groups),
    )
    comparisons: list[dict[str, Any]] = []
    matched_count = 0
    inference_context_change_count = 0
    unmatched_baseline = [
        index
        for group_index in spatial.unmatched_baseline
        for index in baseline_groups[group_index][1]
    ]
    unmatched_candidate = [
        index
        for group_index in spatial.unmatched_candidate
        for index in candidate_groups[group_index][1]
    ]
    for match in spatial.matches:
        baseline_record, baseline_indices, baseline_calls = baseline_groups[match.baseline_index]
        candidate_record, candidate_indices, candidate_calls = candidate_groups[
            match.candidate_index
        ]
        pair_count = min(len(baseline_indices), len(candidate_indices))
        matched_count += pair_count
        unmatched_baseline.extend(baseline_indices[pair_count:])
        unmatched_candidate.extend(candidate_indices[pair_count:])
        inference_context_equal = baseline_calls == candidate_calls
        inference_context_change_count += int(not inference_context_equal)
        comparisons.append(
            {
                "baseline_index": baseline_indices[0],
                "candidate_index": candidate_indices[0],
                "baseline_indices": list(baseline_indices),
                "candidate_indices": list(candidate_indices),
                "baseline_multiplicity": len(baseline_indices),
                "candidate_multiplicity": len(candidate_indices),
                "baseline_inference_calls": list(baseline_calls),
                "candidate_inference_calls": list(candidate_calls),
                "inference_context_equal": inference_context_equal,
                "intersection_over_union": match.intersection_over_union,
                "delta": array_delta(
                    baseline_arrays[str(baseline_record["array_key"])],
                    candidate_arrays[str(candidate_record["array_key"])],
                ),
            }
        )
    return {
        "baseline_count": len(baseline),
        "candidate_count": len(candidate),
        "matched_count": matched_count,
        "unmatched_count": len(unmatched_baseline) + len(unmatched_candidate),
        "unmatched_baseline": sorted(unmatched_baseline),
        "unmatched_candidate": sorted(unmatched_candidate),
        "inference_context_change_count": inference_context_change_count,
        "matches": comparisons,
    }


def _group_array_records(
    records: Sequence[Mapping[str, object]],
    arrays: Mapping[str, np.ndarray],
) -> tuple[tuple[Mapping[str, object], tuple[int, ...], tuple[int, ...]], ...]:
    decorated = sorted(
        (_array_group_key(record), index, record) for index, record in enumerate(records)
    )
    groups: list[tuple[Mapping[str, object], tuple[int, ...], tuple[int, ...]]] = []
    for _, raw_group in groupby(decorated, key=lambda item: item[0]):
        group = tuple(raw_group)
        fingerprints = {
            array_fingerprint(arrays[str(record["array_key"])]) for _, _, record in group
        }
        if len(fingerprints) != 1:
            raise ValueError("duplicate array geometry contains different stage values")
        representative = group[0][2]
        indices = tuple(item[1] for item in group)
        inference_calls = tuple(
            sorted(
                _as_int(record["inference_call"])
                for _, _, record in group
                if "inference_call" in record
            )
        )
        groups.append((representative, indices, inference_calls))
    return tuple(groups)


def _array_group_key(record: Mapping[str, object]) -> str:
    context = {
        "bbox": record_bbox(record),
        **{
            key: record[key]
            for key in ("points", "direction", "text_height", "width")
            if key in record
        },
    }
    return json.dumps(context, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _crops_with_input_shape(
    crops: Sequence[Mapping[str, object]],
    inputs: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    contexts = {
        _as_int(input_record["crop_index"]): {
            key: input_record[key] for key in ("inference_call", "width") if key in input_record
        }
        for input_record in inputs
        if "crop_index" in input_record
    }
    return tuple(
        {**crop, **contexts.get(_as_int(crop["crop_index"]), {})} if "crop_index" in crop else crop
        for crop in crops
    )


def _compare_named_array_stages(
    baseline_record: Mapping[str, Any],
    candidate_record: Mapping[str, Any],
    baseline_arrays: Mapping[str, np.ndarray],
    candidate_arrays: Mapping[str, np.ndarray],
) -> dict[str, Any]:
    baseline_stages = _string_mapping(baseline_record.get("array_stages"))
    candidate_stages = _string_mapping(candidate_record.get("array_stages"))
    comparisons: dict[str, Any] = {}
    for stage_name in sorted(set(baseline_stages) | set(candidate_stages)):
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


def _nested_records(
    record: Mapping[str, Any], parent_key: str, child_key: str
) -> list[Mapping[str, object]]:
    parent = record.get(parent_key)
    if not isinstance(parent, dict):
        raise ValueError(f"probe record {parent_key} must be an object")
    return _record_list(parent, child_key)


def _reviewed_map_zones(record: Mapping[str, Any]) -> Mapping[str, object]:
    detector = record.get("detector")
    if not isinstance(detector, dict):
        raise ValueError("probe record detector must be an object")
    zones = detector.get("reviewed_map_zones", {})
    if not isinstance(zones, dict):
        raise ValueError("probe reviewed map zones must be an object")
    return zones


def _validate_reviewed_map_zones(
    fixture_file: str,
    actual: Mapping[str, object],
    expected: Mapping[str, Sequence[int]],
) -> None:
    required_statistics = {
        "source_zone",
        "map_bounds",
        "value_count",
        "minimum",
        "maximum",
        "mean",
        "percentile_05",
        "percentile_50",
        "percentile_95",
    }
    if set(actual) != set(expected):
        raise ValueError(f"probe is missing reviewed map-zone evidence: {fixture_file}")
    for name, expected_source_zone in expected.items():
        payload = actual[name]
        if not isinstance(payload, dict) or not required_statistics.issubset(payload):
            raise ValueError(f"probe reviewed map-zone evidence is malformed: {fixture_file}")
        source_zone = payload["source_zone"]
        map_bounds = payload["map_bounds"]
        value_count = payload["value_count"]
        statistics = tuple(
            payload[key]
            for key in (
                "minimum",
                "maximum",
                "mean",
                "percentile_05",
                "percentile_50",
                "percentile_95",
            )
        )
        if (
            not isinstance(source_zone, (list, tuple))
            or tuple(source_zone) != tuple(expected_source_zone)
            or not isinstance(map_bounds, (list, tuple))
            or len(map_bounds) != 4
            or not all(
                isinstance(value, int) and not isinstance(value, bool) for value in map_bounds
            )
            or not isinstance(value_count, int)
            or isinstance(value_count, bool)
            or value_count <= 0
            or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in statistics
            )
        ):
            raise ValueError(f"probe reviewed map-zone evidence is malformed: {fixture_file}")


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


def _opencv_identity(manifest: Mapping[str, Any], key: str) -> str:
    opencv = manifest.get("opencv")
    if not isinstance(opencv, dict) or not isinstance(opencv.get(key), str):
        raise ValueError(f"probe manifest OpenCV identity is missing: {key}")
    return str(opencv[key])


def _opencv_runtime_configuration(manifest: Mapping[str, Any]) -> dict[str, object]:
    opencv = manifest.get("opencv")
    if not isinstance(opencv, dict):
        raise ValueError("probe manifest opencv must be an object")
    return {key: opencv.get(key) for key in ("thread_count", "optimized", "opencl")}


def _as_float(value: object) -> float:
    return float(cast(Any, value))


def _as_int(value: object) -> int:
    return int(cast(Any, value))
