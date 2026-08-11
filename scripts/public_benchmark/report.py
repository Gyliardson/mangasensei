from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import cast

from .contracts import (
    EVALUATOR_VERSION,
    METRIC_SPEC_VERSION,
    REPORT_SCHEMA_VERSION,
    BBox,
    CorpusBundle,
    GroundTruthPage,
    GroundTruthRegion,
    Observation,
    ObservationPage,
    ObservedRegion,
)
from .matching import (
    IOU_PPM_SCALE,
    Match,
    assign_one_to_one,
    intersection_union,
    observation_coverage_ppm,
)
from .metrics import (
    detection_metrics,
    ratio_metric,
    reading_order_metrics,
    recognition_slice,
    strict_nfc_compare,
)

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass(slots=True)
class _PageAggregate:
    true_positive: int
    false_positive: int
    false_negative: int
    recognition_records: dict[str, list[dict[str, object]]]
    recognition_gt_totals: dict[str, int]
    order_scored_total: int
    order_matched: int
    order_comparable_pairs: int
    order_inversions: int
    negative_zones_total: int
    negative_zones_hit: int
    negative_hit_pairs: int
    negative_observation_ids: set[str]


def _bbox_json(bbox: BBox) -> dict[str, int]:
    return {"x": bbox.x, "y": bbox.y, "width": bbox.width, "height": bbox.height}


def _polygon_json(polygon: tuple[tuple[int, int], ...] | None) -> list[list[int]] | None:
    if polygon is None:
        return None
    return [[x, y] for x, y in polygon]


def _match_maps(matches: tuple[Match, ...]) -> tuple[dict[str, Match], dict[str, Match]]:
    return (
        {match.ground_truth_id: match for match in matches},
        {match.observation_id: match for match in matches},
    )


def _recognition_record(gt: GroundTruthRegion, observation: ObservedRegion) -> dict[str, object]:
    comparison = strict_nfc_compare(gt.transcription_raw, observation.text)
    return {
        "groundTruthId": gt.id,
        "observationId": observation.id,
        "textForm": gt.text_form,
        "textRole": gt.text_role,
        **comparison,
    }


def _recognition_metrics_for_page(
    page: GroundTruthPage,
    observation_by_id: dict[str, ObservedRegion],
    match_by_gt: dict[str, Match],
) -> tuple[dict[str, object], dict[str, list[dict[str, object]]], dict[str, int]]:
    records: dict[str, list[dict[str, object]]] = {"all": [], "base": [], "ruby": []}
    gt_totals = {"all": 0, "base": 0, "ruby": 0}
    per_region: list[dict[str, object]] = []

    for gt in sorted(page.regions, key=lambda region: region.id):
        if not gt.recognition_scored:
            continue
        gt_totals["all"] += 1
        gt_totals[gt.text_form] += 1
        match = match_by_gt.get(gt.id)
        if match is None:
            per_region.append(
                {
                    "groundTruthId": gt.id,
                    "textForm": gt.text_form,
                    "textRole": gt.text_role,
                    "status": "not-measured",
                    "reason": "missing-spatial-match",
                }
            )
            continue
        observation = observation_by_id[match.observation_id]
        record = _recognition_record(gt, observation)
        records["all"].append(record)
        records[gt.text_form].append(record)
        per_region.append({"status": "measured", "reason": None, **record})

    metrics = {
        "normalization": "strict-nfc-v1",
        "slices": {
            "all": recognition_slice(records["all"], gt_totals["all"]),
            "base": recognition_slice(records["base"], gt_totals["base"]),
            "ruby": recognition_slice(records["ruby"], gt_totals["ruby"]),
        },
        "perRegion": per_region,
    }
    return metrics, records, gt_totals


def _reading_metrics_for_page(
    page: GroundTruthPage,
    observation_by_id: dict[str, ObservedRegion],
    match_by_gt: dict[str, Match],
) -> dict[str, object]:
    scored = [region for region in page.regions if region.reading_order_scored]
    order_pairs: list[tuple[int, int]] = []
    for gt in scored:
        match = match_by_gt.get(gt.id)
        if match is None:
            continue
        assert gt.reading_order_position is not None
        order_pairs.append(
            (gt.reading_order_position, observation_by_id[match.observation_id].reading_order)
        )

    if not scored:
        metrics = reading_order_metrics(
            scored_total=0,
            matched_pairs=[],
            exact_sequence_matches=None,
        )
        metrics["exactSequence"] = {
            "status": "not-applicable",
            "reason": "no-scored-regions",
            "matches": None,
        }
        return metrics

    exact: bool | None = None
    if len(order_pairs) == len(scored):
        gt_by_observed_order = [
            gt_id
            for _, gt_id in sorted(
                (
                    (observation_by_id[match_by_gt[gt.id].observation_id].reading_order, gt.id)
                    for gt in scored
                ),
                key=lambda item: (item[0], item[1]),
            )
        ]
        exact = tuple(gt_by_observed_order) == page.reading_order_sequence

    return reading_order_metrics(
        scored_total=len(scored),
        matched_pairs=order_pairs,
        exact_sequence_matches=exact,
    )


def _ignored_unscored_observations(
    page: GroundTruthPage,
    observations: tuple[ObservedRegion, ...],
    match_by_gt: dict[str, Match],
    match_by_observation: dict[str, Match],
) -> list[dict[str, object]]:
    gt_by_id = {region.id: region for region in page.regions}
    unscored_gt = tuple(
        sorted(
            (region for region in page.regions if not region.detection_scored),
            key=lambda region: region.id,
        )
    )
    ignored: list[dict[str, object]] = []

    for observation in sorted(observations, key=lambda region: region.id):
        matched = match_by_observation.get(observation.id)
        if matched is not None:
            matched_gt = gt_by_id[matched.ground_truth_id]
            if not matched_gt.detection_scored:
                intersection, denominator, ppm = observation_coverage_ppm(
                    observation.bbox, matched_gt.bbox
                )
                ignored.append(
                    {
                        "observationId": observation.id,
                        "groundTruthId": matched_gt.id,
                        "reason": "matched_detection_unscored_ground_truth",
                        "observationCoverage": {
                            "status": "measured",
                            "numerator": intersection,
                            "denominator": denominator,
                            "ppm": ppm,
                        },
                    }
                )
            continue

        best: tuple[Fraction, GroundTruthRegion, int, int, int] | None = None
        for gt in unscored_gt:
            intersection, denominator, ppm = observation_coverage_ppm(observation.bbox, gt.bbox)
            if 2 * intersection < denominator:
                continue
            candidate = (Fraction(intersection, denominator), gt, intersection, denominator, ppm)
            if best is None or candidate[0] > best[0] or (
                candidate[0] == best[0] and candidate[1].id < best[1].id
            ):
                best = candidate
        if best is not None:
            _, gt, intersection, denominator, ppm = best
            ignored.append(
                {
                    "observationId": observation.id,
                    "groundTruthId": gt.id,
                    "reason": "ignored_unscored_ground_truth",
                    "observationCoverage": {
                        "status": "measured",
                        "numerator": intersection,
                        "denominator": denominator,
                        "ppm": ppm,
                    },
                }
            )

    return ignored


def _negative_zone_metrics(
    page: GroundTruthPage,
    observations: tuple[ObservedRegion, ...],
) -> tuple[dict[str, object], set[str]]:
    zone_details: list[dict[str, object]] = []
    total_hit_pairs = 0
    hit_observation_ids: set[str] = set()
    zones_hit = 0

    sorted_observations = tuple(sorted(observations, key=lambda region: region.id))
    for zone in sorted(page.negative_zones, key=lambda item: item.id):
        hit_pairs: list[dict[str, object]] = []
        best_observation: tuple[Fraction, int, int, str] | None = None
        best_zone: tuple[Fraction, int, int, str] | None = None
        for observation in sorted_observations:
            intersection, _ = intersection_union(observation.bbox, zone.bbox)
            observation_denominator = observation.bbox.area
            zone_denominator = zone.bbox.area
            observation_fraction = Fraction(intersection, observation_denominator)
            zone_fraction = Fraction(intersection, zone_denominator)
            observation_candidate = (
                observation_fraction,
                intersection,
                observation_denominator,
                observation.id,
            )
            zone_candidate = (zone_fraction, intersection, zone_denominator, observation.id)
            if best_observation is None or observation_candidate[0] > best_observation[0] or (
                observation_candidate[0] == best_observation[0]
                and observation.id < best_observation[3]
            ):
                best_observation = observation_candidate
            if best_zone is None or zone_candidate[0] > best_zone[0] or (
                zone_candidate[0] == best_zone[0] and observation.id < best_zone[3]
            ):
                best_zone = zone_candidate

            if 2 * intersection >= observation_denominator or 2 * intersection >= zone_denominator:
                hit_pairs.append(
                    {
                        "observationId": observation.id,
                        "intersectionArea": intersection,
                        "observationCoverage": ratio_metric(intersection, observation_denominator),
                        "zoneCoverage": ratio_metric(intersection, zone_denominator),
                    }
                )
                hit_observation_ids.add(observation.id)

        if hit_pairs:
            zones_hit += 1
        total_hit_pairs += len(hit_pairs)
        if best_observation is None:
            max_observation_coverage: dict[str, object] = {
                "status": "not-applicable",
                "numerator": 0,
                "denominator": 0,
                "decimal": None,
            }
            max_zone_coverage = dict(max_observation_coverage)
        else:
            max_observation_coverage = ratio_metric(best_observation[1], best_observation[2])
            assert best_zone is not None
            max_zone_coverage = ratio_metric(best_zone[1], best_zone[2])

        zone_details.append(
            {
                "zoneId": zone.id,
                "kind": zone.kind,
                "hit": bool(hit_pairs),
                "hitPairCount": len(hit_pairs),
                "observationIds": sorted(
                    cast(str, pair["observationId"]) for pair in hit_pairs
                ),
                "maxObservationCoverage": max_observation_coverage,
                "maxZoneCoverage": max_zone_coverage,
                "hitPairs": hit_pairs,
            }
        )

    return (
        {
            "zonesTotal": len(page.negative_zones),
            "zonesHit": zones_hit,
            "zoneHitRate": ratio_metric(
                zones_hit, len(page.negative_zones), zero_status="not-applicable"
            ),
            "hitPairCount": total_hit_pairs,
            "uniqueObservedRegionsHittingZones": len(hit_observation_ids),
            "zones": zone_details,
        },
        hit_observation_ids,
    )


def _page_report(
    page: GroundTruthPage, observed_page: ObservationPage
) -> tuple[dict[str, object], _PageAggregate]:
    observations = observed_page.regions
    observations_by_id = {region.id: region for region in observations}
    gt_by_id = {region.id: region for region in page.regions}
    matches = assign_one_to_one(page.regions, observations)
    match_by_gt, match_by_observation = _match_maps(matches)

    ignored = _ignored_unscored_observations(
        page, observations, match_by_gt, match_by_observation
    )
    ignored_ids = {str(item["observationId"]) for item in ignored}

    true_positive = sum(
        1 for gt in page.regions if gt.detection_scored and gt.id in match_by_gt
    )
    false_negative = sum(
        1 for gt in page.regions if gt.detection_scored and gt.id not in match_by_gt
    )
    matched_scored_observations = {
        match.observation_id
        for match in matches
        if gt_by_id[match.ground_truth_id].detection_scored
    }
    false_positive_ids = sorted(
        region.id
        for region in observations
        if region.id not in matched_scored_observations and region.id not in ignored_ids
    )
    false_positive = len(false_positive_ids)

    match_details: list[dict[str, object]] = []
    for match in matches:
        gt = gt_by_id[match.ground_truth_id]
        observation = observations_by_id[match.observation_id]
        match_details.append(
            {
                "groundTruthId": gt.id,
                "observationId": observation.id,
                "intersectionArea": match.intersection_area,
                "unionArea": match.union_area,
                "iouPpm": match.iou_ppm,
                "groundTruthBbox": _bbox_json(gt.bbox),
                "observedBbox": _bbox_json(observation.bbox),
                "observedPolygon": _polygon_json(observation.polygon),
                "scoring": {
                    "detection": gt.detection_scored,
                    "recognition": gt.recognition_scored,
                    "readingOrder": gt.reading_order_scored,
                },
            }
        )

    unmatched_gt = [
        {
            "groundTruthId": gt.id,
            "bbox": _bbox_json(gt.bbox),
            "scoring": {
                "detection": gt.detection_scored,
                "recognition": gt.recognition_scored,
                "readingOrder": gt.reading_order_scored,
            },
        }
        for gt in sorted(page.regions, key=lambda region: region.id)
        if gt.id not in match_by_gt
    ]
    unmatched_observations = [
        {
            "observationId": observation.id,
            "bbox": _bbox_json(observation.bbox),
            "polygon": _polygon_json(observation.polygon),
        }
        for observation in sorted(observations, key=lambda region: region.id)
        if observation.id not in match_by_observation and observation.id not in ignored_ids
    ]

    recognition, recognition_records, recognition_gt_totals = _recognition_metrics_for_page(
        page, observations_by_id, match_by_gt
    )
    reading_order = _reading_metrics_for_page(page, observations_by_id, match_by_gt)
    negative_zones, negative_observation_ids = _negative_zone_metrics(page, observations)

    page_report = {
        "pageId": page.id,
        "detection": detection_metrics(true_positive, false_positive, false_negative),
        "recognition": recognition,
        "readingOrder": reading_order,
        "negativeZones": negative_zones,
        "matches": match_details,
        "unmatchedGroundTruth": unmatched_gt,
        "unmatchedObservations": unmatched_observations,
        "ignoredObservations": sorted(
            ignored, key=lambda item: (str(item["observationId"]), str(item["groundTruthId"]))
        ),
    }

    aggregate = _PageAggregate(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        recognition_records=recognition_records,
        recognition_gt_totals=recognition_gt_totals,
        order_scored_total=sum(1 for region in page.regions if region.reading_order_scored),
        order_matched=sum(
            1 for region in page.regions if region.reading_order_scored and region.id in match_by_gt
        ),
        order_comparable_pairs=cast(int, reading_order["comparablePairCount"]),
        order_inversions=cast(int, reading_order["inversionCount"]),
        negative_zones_total=cast(int, negative_zones["zonesTotal"]),
        negative_zones_hit=cast(int, negative_zones["zonesHit"]),
        negative_hit_pairs=cast(int, negative_zones["hitPairCount"]),
        negative_observation_ids=negative_observation_ids,
    )
    return page_report, aggregate


def _aggregate_recognition(page_aggregates: list[_PageAggregate]) -> dict[str, object]:
    result: dict[str, object] = {}
    for slice_name in ("all", "base", "ruby"):
        records: list[dict[str, object]] = []
        gt_total = 0
        for aggregate in page_aggregates:
            records.extend(aggregate.recognition_records[slice_name])
            gt_total += aggregate.recognition_gt_totals[slice_name]
        result[slice_name] = recognition_slice(records, gt_total)
    return {"normalization": "strict-nfc-v1", "slices": result}


def _aggregate_reading_order(page_aggregates: list[_PageAggregate]) -> dict[str, object]:
    total_scored = sum(item.order_scored_total for item in page_aggregates)
    total_matched = sum(item.order_matched for item in page_aggregates)
    comparable = sum(item.order_comparable_pairs for item in page_aggregates)
    inversions = sum(item.order_inversions for item in page_aggregates)
    return {
        "orderCoverage": ratio_metric(total_matched, total_scored, zero_status="not-applicable"),
        "comparablePairCount": comparable,
        "inversionCount": inversions,
        "pairwiseOrderingAccuracyOnMatchedRegions": ratio_metric(
            comparable - inversions, comparable
        ),
        "normalizedKendallInversionDistance": ratio_metric(inversions, comparable),
        "aggregation": "sum comparable within-page pairs only; no cross-page ordering pairs",
    }


def _aggregate_negative_zones(page_aggregates: list[_PageAggregate]) -> dict[str, object]:
    zones_total = sum(item.negative_zones_total for item in page_aggregates)
    zones_hit = sum(item.negative_zones_hit for item in page_aggregates)
    hit_pairs = sum(item.negative_hit_pairs for item in page_aggregates)
    observation_ids: set[str] = set()
    for item in page_aggregates:
        observation_ids.update(item.negative_observation_ids)
    return {
        "zonesTotal": zones_total,
        "zonesHit": zones_hit,
        "zoneHitRate": ratio_metric(zones_hit, zones_total, zero_status="not-applicable"),
        "hitPairCount": hit_pairs,
        "uniqueObservedRegionsHittingZones": len(observation_ids),
    }


def _unsupported_metrics() -> list[dict[str, str]]:
    return [
        {
            "family": "furigana-relationship",
            "status": "unsupported",
            "reason": "observation-v1 does not expose ruby-to-base relationships",
        },
        {
            "family": "linguistics-jmdict",
            "status": "unsupported",
            "reason": "public OCR corpus lacks complete linguistic ground truth",
        },
        {
            "family": "presentation-marks-boten",
            "status": "unsupported",
            "reason": "observation-v1 does not expose presentation marks",
        },
        {
            "family": "text-role-classification",
            "status": "unsupported",
            "reason": "observation-v1 does not expose predicted text roles",
        },
    ]


def build_report(
    corpus: CorpusBundle,
    observation: Observation,
    *,
    evaluator_repository_sha: str,
) -> dict[str, object]:
    if _HEX40.fullmatch(evaluator_repository_sha) is None:
        raise ValueError("evaluator_repository_sha must be a lowercase 40-character Git SHA")

    observed_by_page = {page.id: page for page in observation.pages}
    page_reports: list[dict[str, object]] = []
    page_aggregates: list[_PageAggregate] = []
    for page in corpus.pages:
        page_report, aggregate = _page_report(page, observed_by_page[page.id])
        page_reports.append(page_report)
        page_aggregates.append(aggregate)

    true_positive = sum(item.true_positive for item in page_aggregates)
    false_positive = sum(item.false_positive for item in page_aggregates)
    false_negative = sum(item.false_negative for item in page_aggregates)
    detection = detection_metrics(true_positive, false_positive, false_negative)
    recognition = _aggregate_recognition(page_aggregates)
    reading_order = _aggregate_reading_order(page_aggregates)
    negative_zones = _aggregate_negative_zones(page_aggregates)

    corpus_pages = [
        {
            "pageId": page.id,
            "imageSha256": page.image_sha256,
            "annotationSha256": page.annotation_sha256,
            "width": page.width,
            "height": page.height,
        }
        for page in corpus.pages
    ]

    recognition_slices = cast(dict[str, object], recognition["slices"])
    all_recognition = cast(dict[str, object], recognition_slices["all"])
    public_summary = {
        "scope": "MangaSensei Public Demo Corpus v1",
        "detection": {
            "precision": detection["precision"],
            "recall": detection["recall"],
            "f1": detection["f1"],
        },
        "recognition": {
            "coverage": all_recognition["coverage"],
            "exactMatchRate": all_recognition["exactMatchRate"],
            "microCer": all_recognition["microCer"],
        },
        "readingOrder": {
            "label": "pairwise ordering accuracy on matched regions",
            "coverage": reading_order["orderCoverage"],
            "pairwiseAccuracy": reading_order["pairwiseOrderingAccuracyOnMatchedRegions"],
        },
        "graphicalNegativeZones": {"zoneHitRate": negative_zones["zoneHitRate"]},
        "claimBoundary": (
            "Corpus-specific result; not a universal Japanese manga OCR accuracy claim."
        ),
    }

    return {
        "schemaVersion": REPORT_SCHEMA_VERSION,
        "metricSpecVersion": METRIC_SPEC_VERSION,
        "evaluator": {
            "version": EVALUATOR_VERSION,
            "repositorySha": evaluator_repository_sha,
        },
        "sourceObservation": {
            "schemaVersion": observation.schema_version,
            "kind": observation.kind,
            "sha256": observation.sha256,
            "producer": observation.producer,
            "features": observation.features,
            "ocr": observation.ocr,
            "runtime": observation.runtime,
        },
        "corpus": {
            "id": corpus.corpus_id,
            "schemaVersion": corpus.schema_version,
            "manifestSha256": corpus.manifest_sha256,
            "annotationSchemaSha256": corpus.annotation_schema_sha256,
            "pages": corpus_pages,
        },
        "matching": {
            "geometry": "axis-aligned-bbox-iou",
            "eligibility": "2 * intersection_area >= union_area",
            "threshold": {"numerator": 1, "denominator": 2, "decimal": "0.500000"},
            "assignment": "global-one-to-one-hungarian",
            "optimizationPriority": [
                "maximum-eligible-match-cardinality",
                "maximum-aggregate-exact-iou",
                "stable-sorted-id-and-fixed-traversal-tie-break",
            ],
            "iouDiagnosticScalePpm": IOU_PPM_SCALE,
        },
        "recognitionNormalization": {
            "contract": "strict-nfc-v1",
            "groundTruth": "must already be NFC; preserved exactly",
            "observation": "NFC-normalize comparison representation only",
            "forbiddenTransforms": [
                "nfkc",
                "width-folding",
                "kana-folding",
                "punctuation-removal",
                "whitespace-trim-or-collapse",
                "dictionary-correction",
                "spelling-correction",
            ],
        },
        "negativeZoneConfiguration": {
            "geometry": "axis-aligned-bbox-overlap",
            "hitRule": "observation coverage >= 0.50 OR zone coverage >= 0.50",
            "threshold": {"numerator": 1, "denominator": 2, "decimal": "0.500000"},
            "lexicalContentUsed": False,
        },
        "pages": page_reports,
        "aggregate": {
            "detection": detection,
            "recognition": recognition,
            "readingOrder": reading_order,
            "negativeZones": negative_zones,
        },
        "unsupportedMetricFamilies": _unsupported_metrics(),
        "warnings": [],
        "publicSummary": public_summary,
    }


def serialize_report(report: dict[str, object]) -> bytes:
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
