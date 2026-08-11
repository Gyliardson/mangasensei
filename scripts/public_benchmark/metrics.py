from __future__ import annotations

import unicodedata
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
from typing import TypeAlias

Metric: TypeAlias = dict[str, object]


def _record_int(record: dict[str, object], key: str) -> int:
    value = record[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"recognition record {key} must be an integer")
    return value


_DECIMAL_QUANTUM = Decimal("0.000001")


def _decimal_string(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 60
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
        return format(decimal_value.quantize(_DECIMAL_QUANTUM, rounding=ROUND_HALF_UP), "f")


def ratio_metric(
    numerator: int,
    denominator: int,
    *,
    zero_status: str = "insufficient-data",
) -> Metric:
    if denominator < 0 or numerator < 0:
        raise ValueError("ratio counts must be non-negative")
    if denominator == 0:
        return {
            "status": zero_status,
            "numerator": numerator,
            "denominator": denominator,
            "decimal": None,
        }
    value = Fraction(numerator, denominator)
    return {
        "status": "measured",
        "numerator": numerator,
        "denominator": denominator,
        "decimal": _decimal_string(value),
    }


def fraction_metric(value: Fraction, *, components: int) -> Metric:
    return {
        "status": "measured",
        "numerator": value.numerator,
        "denominator": value.denominator,
        "decimal": _decimal_string(value),
        "components": components,
    }


def levenshtein_codepoints(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) < len(right):
        left, right = right, left

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insertion = current[right_index - 1] + 1
            deletion = previous[right_index] + 1
            substitution = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insertion, deletion, substitution))
        previous = current
    return previous[-1]


def strict_nfc_compare(ground_truth: str, observation_raw: str) -> dict[str, object]:
    if not unicodedata.is_normalized("NFC", ground_truth):
        raise ValueError("ground truth must already be NFC")
    normalized_observation = unicodedata.normalize("NFC", observation_raw)
    edit_distance = levenshtein_codepoints(ground_truth, normalized_observation)
    return {
        "rawExactlyEqual": observation_raw == ground_truth,
        "observationRawWasNfc": unicodedata.is_normalized("NFC", observation_raw),
        "exactMatch": normalized_observation == ground_truth,
        "editDistance": edit_distance,
        "groundTruthCharacters": len(ground_truth),
    }


def detection_metrics(
    true_positive: int, false_positive: int, false_negative: int
) -> dict[str, object]:
    precision = ratio_metric(true_positive, true_positive + false_positive)
    recall = ratio_metric(true_positive, true_positive + false_negative)
    f1_denominator = 2 * true_positive + false_positive + false_negative
    f1 = ratio_metric(2 * true_positive, f1_denominator)
    return {
        "counts": {
            "truePositive": true_positive,
            "falsePositive": false_positive,
            "falseNegative": false_negative,
        },
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def recognition_slice(
    records: list[dict[str, object]], ground_truth_total: int
) -> dict[str, object]:
    matched = len(records)
    exact = sum(1 for record in records if record["exactMatch"] is True)
    total_edit = sum(_record_int(record, "editDistance") for record in records)
    total_gt_chars = sum(_record_int(record, "groundTruthCharacters") for record in records)
    individual_cers = [
        Fraction(
            _record_int(record, "editDistance"),
            _record_int(record, "groundTruthCharacters"),
        )
        for record in records
    ]

    if matched == 0:
        macro_cer: Metric = {
            "status": "insufficient-data",
            "numerator": 0,
            "denominator": 0,
            "decimal": None,
            "components": 0,
        }
    else:
        macro_cer = fraction_metric(
            sum(individual_cers, start=Fraction(0, 1)) / matched, components=matched
        )

    return {
        "groundTruthTotal": ground_truth_total,
        "matched": matched,
        "coverage": ratio_metric(matched, ground_truth_total, zero_status="not-applicable"),
        "exactMatchCount": exact,
        "exactMatchRate": ratio_metric(exact, matched),
        "totalEditDistance": total_edit,
        "totalGroundTruthCharacters": total_gt_chars,
        "microCer": ratio_metric(total_edit, total_gt_chars),
        "macroCer": macro_cer,
    }


def reading_order_metrics(
    *,
    scored_total: int,
    matched_pairs: list[tuple[int, int]],
    exact_sequence_matches: bool | None,
) -> dict[str, object]:
    matched_pairs.sort(key=lambda pair: pair[0])
    matched_count = len(matched_pairs)
    comparable = matched_count * (matched_count - 1) // 2
    inversions = 0
    for left_index in range(matched_count):
        for right_index in range(left_index + 1, matched_count):
            if matched_pairs[left_index][1] > matched_pairs[right_index][1]:
                inversions += 1

    if exact_sequence_matches is None:
        exact_sequence: dict[str, object] = {
            "status": "not-measured",
            "reason": "incomplete-region-matching",
            "matches": None,
        }
    else:
        exact_sequence = {"status": "measured", "reason": None, "matches": exact_sequence_matches}

    return {
        "orderCoverage": ratio_metric(matched_count, scored_total, zero_status="not-applicable"),
        "comparablePairCount": comparable,
        "inversionCount": inversions,
        "pairwiseOrderingAccuracyOnMatchedRegions": ratio_metric(
            comparable - inversions, comparable
        ),
        "normalizedKendallInversionDistance": ratio_metric(inversions, comparable),
        "exactSequence": exact_sequence,
    }
