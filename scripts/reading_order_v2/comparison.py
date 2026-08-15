from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId

from .canonical import canonical_json_bytes, sha256_bytes
from .contracts import (
    PAGE_IDS,
    REQUIRED_SLICES,
    ContractError,
    PageGroundTruth,
    QualificationPair,
    load_ground_truth,
)
from .scoring import CorpusScore
from .validate_corpus import validate_corpus
from .verdict import GateReason, Verdict, VerdictResult, evaluate_verdict

SPEC_VERSION = "reading-order-v2-experiment-spec-v1"
COMPARISON_SCHEMA_VERSION = "reading-order-v2-comparison-v1"
REPEAT_HASH_FIELDS = (
    "diagnosticsSha256",
    "orderingSha256",
    "scoresSha256",
)
_ASSIGNMENT_FALLBACK_REASONS = {
    "region-unassigned-or-ambiguous",
    "fewer-than-two-nonempty-groups",
}


def build_repeat_hash_record(
    diagnostics: object, ordering: object, scores: CorpusScore
) -> dict[str, str]:
    return {
        "diagnosticsSha256": sha256_bytes(canonical_json_bytes(diagnostics)),
        "orderingSha256": sha256_bytes(canonical_json_bytes(ordering)),
        "scoresSha256": sha256_bytes(canonical_json_bytes(scores)),
    }


def require_repeat_determinism(arm: ArmId | str, records: Sequence[Mapping[str, str]]) -> None:
    arm_name = arm.value if isinstance(arm, ArmId) else arm
    if len(records) != 3:
        raise RuntimeError(f"{arm_name}: exactly three repeat hash records are required")
    for index, record in enumerate(records, start=1):
        if set(record) != set(REPEAT_HASH_FIELDS):
            raise RuntimeError(f"{arm_name}: repeat {index} has malformed hash fields")
        if any(
            re.fullmatch(r"[0-9a-f]{64}", record[field]) is None
            for field in REPEAT_HASH_FIELDS
        ):
            raise RuntimeError(f"{arm_name}: repeat {index} has malformed SHA-256 values")
    mismatched = [
        field for field in REPEAT_HASH_FIELDS if len({record[field] for record in records}) != 1
    ]
    if mismatched:
        raise RuntimeError(
            f"{arm_name}: nondeterministic evidence across fresh-process repeats: "
            + ", ".join(mismatched)
        )


def _score_slice_failures(score: CorpusScore, arm: ArmId) -> list[str]:
    failures: list[str] = []
    for name in sorted(REQUIRED_SLICES):
        metric = score.slices.get(name)
        if metric is None:
            failures.append(f"{arm.value}: missing required score slice {name}")
        elif metric.comparable_pairs <= 0:
            failures.append(f"{arm.value}: required score slice {name} has no comparable pairs")
    return failures


def _artifact_failures(
    diagnostics_by_arm: Mapping[ArmId, Sequence[dict[str, object]]],
    ordering_by_arm: Mapping[ArmId, Sequence[dict[str, object]]],
    repeat_hashes_by_arm: Mapping[ArmId, Sequence[Mapping[str, str]]],
    scores_by_arm: Mapping[ArmId, CorpusScore],
) -> list[str]:
    failures: list[str] = []
    required_arms = set(ArmId)
    for label, mapping in (
        ("diagnostics", diagnostics_by_arm),
        ("ordering", ordering_by_arm),
        ("repeat hashes", repeat_hashes_by_arm),
        ("scores", scores_by_arm),
    ):
        if set(mapping) != required_arms:
            missing = sorted(arm.value for arm in required_arms - set(mapping))
            extra = sorted(str(arm) for arm in set(mapping) - required_arms)
            failures.append(f"{label}: arm inventory mismatch missing={missing} extra={extra}")
    if failures:
        return failures

    for arm in ArmId:
        diagnostics = diagnostics_by_arm[arm]
        ordering = ordering_by_arm[arm]
        try:
            repeat_hashes = repeat_hashes_by_arm[arm]
            require_repeat_determinism(arm, repeat_hashes)
            selected_hashes = build_repeat_hash_record(
                diagnostics, ordering, scores_by_arm[arm]
            )
            for field in REPEAT_HASH_FIELDS:
                if selected_hashes[field] != repeat_hashes[0][field]:
                    failures.append(
                        f"{arm.value}: selected artifact does not match repeat hash {field}"
                    )
        except RuntimeError as error:
            failures.append(str(error))
        diagnostic_pages = [item.get("pageId") for item in diagnostics]
        ordering_pages = [item.get("pageId") for item in ordering]
        if diagnostic_pages != list(PAGE_IDS):
            failures.append(f"{arm.value}: diagnostics must be exactly ordered H01..H16")
        if ordering_pages != list(PAGE_IDS):
            failures.append(f"{arm.value}: ordering must be exactly ordered H01..H16")
        if len(diagnostics) != len(ordering):
            failures.append(f"{arm.value}: diagnostics/ordering page counts differ")
            continue
        for diagnostic, order in zip(diagnostics, ordering, strict=True):
            page_id = diagnostic.get("pageId")
            required_fields = {
                "inputRegionCount",
                "inputRegionIds",
                "finalOrder",
                "segmentation",
                "groups",
                "regions",
                "usedPanelEvidence",
                "panelEvidenceMode",
                "fallbackReason",
            }
            if not required_fields <= set(diagnostic):
                failures.append(f"{arm.value}/{page_id}: required diagnostics missing")
                continue
            input_count = diagnostic.get("inputRegionCount")
            input_ids = diagnostic.get("inputRegionIds")
            final_order = diagnostic.get("finalOrder")
            ordering_final = order.get("finalOrder")
            if (
                not isinstance(input_count, int)
                or isinstance(input_count, bool)
                or not isinstance(input_ids, list)
                or not isinstance(final_order, list)
                or not all(isinstance(item, str) for item in input_ids + final_order)
            ):
                failures.append(f"{arm.value}/{page_id}: malformed region integrity fields")
                continue
            if (
                input_count != len(input_ids)
                or len(set(input_ids)) != len(input_ids)
                or len(set(final_order)) != len(final_order)
                or len(final_order) != input_count
                or set(final_order) != set(input_ids)
                or ordering_final != final_order
            ):
                failures.append(f"{arm.value}/{page_id}: region integrity evidence failed")
        score = scores_by_arm[arm]
        if score.page_count != len(PAGE_IDS):
            failures.append(f"{arm.value}: score page_count must be 16")
        score_page_ids = [page.page_id for page in score.pages]
        if score_page_ids != list(PAGE_IDS):
            failures.append(f"{arm.value}: score pages must be exactly ordered H01..H16")
        failures.extend(_score_slice_failures(score, arm))
    return failures


def _slice_pages(ground_truth: Mapping[str, PageGroundTruth], name: str) -> set[str]:
    return {
        page_id
        for page_id, page in ground_truth.items()
        if any(name in pair.slices for pair in page.qualification_pairs)
    }


def _diag_index(
    diagnostics: Sequence[dict[str, object]],
) -> dict[str, dict[str, object]]:
    return {
        page_id: item
        for item in diagnostics
        if isinstance((page_id := item.get("pageId")), str)
    }


def _segmentation_reliable(diagnostic: Mapping[str, object]) -> bool:
    value = diagnostic.get("segmentation")
    return isinstance(value, dict) and value.get("reliable") is True


def _confident_group_count(diagnostic: Mapping[str, object]) -> int:
    groups = diagnostic.get("groups")
    if not isinstance(groups, list):
        return 0
    return sum(
        isinstance(group, dict)
        and isinstance(group.get("confidentRegionIds"), list)
        and bool(group["confidentRegionIds"])
        for group in groups
    )


def _has_uncertain_region(diagnostic: Mapping[str, object]) -> bool:
    regions = diagnostic.get("regions")
    if not isinstance(regions, list):
        return False
    return any(
        isinstance(region, dict)
        and region.get("assignmentStatus") in {"unassigned", "ambiguous"}
        for region in regions
    )


def _region_index(diagnostic: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    regions = diagnostic.get("regions")
    if not isinstance(regions, list):
        return {}
    return {
        region_id: region
        for region in regions
        if isinstance(region, dict)
        and isinstance((region_id := region.get("regionId")), str)
    }


def _is_b_pair(pair: QualificationPair) -> bool:
    return "B" in pair.slices or "A+B" in pair.slices


def _same_nonempty_field(
    first: Mapping[str, object], second: Mapping[str, object], field: str
) -> bool:
    value = first.get(field)
    return isinstance(value, str) and bool(value) and second.get(field) == value


def _pair_bound_b_evidence(
    page: PageGroundTruth,
    diagnostic: Mapping[str, object],
) -> dict[str, tuple[str, ...]]:
    """Return qualification-pair IDs that actually exercise each frozen B relation."""
    result: dict[str, list[str]] = {"horizontal": [], "mixed": [], "vertical": []}
    if diagnostic.get("usedPanelEvidence") is not True:
        return {name: () for name in result}
    regions = _region_index(diagnostic)
    for pair in page.qualification_pairs:
        first = regions.get(pair.earlier)
        second = regions.get(pair.later)
        if first is None or second is None:
            continue
        same_tier = _same_nonempty_field(first, second, "localTierId")
        same_run = _same_nonempty_field(first, second, "localRunId")
        first_mode = first.get("localOrderingMode")
        second_mode = second.get("localOrderingMode")
        first_orientation = first.get("orientationClass")
        second_orientation = second.get("orientationClass")

        if (
            _is_b_pair(pair)
            and "horizontal-only" in pair.slices
            and same_tier
            and same_run
            and first_mode == second_mode == "ltr-horizontal"
            and first_orientation == second_orientation == "horizontal"
        ):
            result["horizontal"].append(pair.pair_id)

        if (
            _is_b_pair(pair)
            and "mixed" in pair.slices
            and same_tier
            and not same_run
            and isinstance(first.get("localRunId"), str)
            and isinstance(second.get("localRunId"), str)
            and first_mode == second_mode == "mixed"
            and {first_orientation, second_orientation} == {"horizontal", "vertical"}
        ):
            result["mixed"].append(pair.pair_id)

        if (
            "vertical-only" in pair.slices
            and same_tier
            and same_run
            and first_mode == second_mode == "rtl-vertical"
            and first_orientation == second_orientation == "vertical"
        ):
            result["vertical"].append(pair.pair_id)
    return {name: tuple(sorted(pair_ids)) for name, pair_ids in result.items()}


def _derive_a_exercise(
    ground_truth: Mapping[str, PageGroundTruth],
    control: Mapping[str, dict[str, object]],
    panel_only: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    declared = _slice_pages(ground_truth, "A") | _slice_pages(ground_truth, "A+B")
    conditions: dict[str, set[str]] = {
        "reliableSegmentation": set(),
        "twoConfidentPopulatedGroups": set(),
        "uncertainRegion": set(),
        "a0AssignmentFallback": set(),
        "a1PartialPanelEvidenceUsed": set(),
    }
    for page_id in sorted(declared):
        a0 = control.get(page_id, {})
        a1 = panel_only.get(page_id, {})
        if _segmentation_reliable(a0) and _segmentation_reliable(a1):
            conditions["reliableSegmentation"].add(page_id)
        if _confident_group_count(a1) >= 2:
            conditions["twoConfidentPopulatedGroups"].add(page_id)
        if _has_uncertain_region(a1):
            conditions["uncertainRegion"].add(page_id)
        if (
            a0.get("usedPanelEvidence") is False
            and a0.get("fallbackReason") in _ASSIGNMENT_FALLBACK_REASONS
        ):
            conditions["a0AssignmentFallback"].add(page_id)
        if (
            a1.get("usedPanelEvidence") is True
            and a1.get("panelEvidenceMode") == "partial"
        ):
            conditions["a1PartialPanelEvidenceUsed"].add(page_id)
    qualifying = set(declared)
    for pages in conditions.values():
        qualifying &= pages
    return {
        "exercised": bool(qualifying),
        "declaredPages": sorted(declared),
        "conditionPages": {name: sorted(pages) for name, pages in sorted(conditions.items())},
        "qualifyingPages": sorted(qualifying),
    }


def _derive_b_exercise(
    ground_truth: Mapping[str, PageGroundTruth],
    order_only: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    b_pages = _slice_pages(ground_truth, "B") | _slice_pages(ground_truth, "A+B")
    horizontal_declared = b_pages & _slice_pages(ground_truth, "horizontal-only")
    mixed_declared = b_pages & _slice_pages(ground_truth, "mixed")
    vertical_declared = _slice_pages(ground_truth, "vertical-only")

    horizontal_pairs: dict[str, tuple[str, ...]] = {}
    mixed_pairs: dict[str, tuple[str, ...]] = {}
    vertical_pairs: dict[str, tuple[str, ...]] = {}
    for page_id, page in sorted(ground_truth.items()):
        evidence = _pair_bound_b_evidence(page, order_only.get(page_id, {}))
        if page_id in horizontal_declared and evidence["horizontal"]:
            horizontal_pairs[page_id] = evidence["horizontal"]
        if page_id in mixed_declared and evidence["mixed"]:
            mixed_pairs[page_id] = evidence["mixed"]
        if page_id in vertical_declared and evidence["vertical"]:
            vertical_pairs[page_id] = evidence["vertical"]

    horizontal = set(horizontal_pairs)
    mixed = set(mixed_pairs)
    vertical = set(vertical_pairs)
    exercised = bool(horizontal and mixed and vertical)
    return {
        "exercised": exercised,
        "bDeclaredPages": sorted(b_pages),
        "horizontalDeclaredPages": sorted(horizontal_declared),
        "mixedDeclaredPages": sorted(mixed_declared),
        "verticalControlDeclaredPages": sorted(vertical_declared),
        "horizontalLtrPages": sorted(horizontal),
        "mixedOrientationPages": sorted(mixed),
        "verticalRtlControlPages": sorted(vertical),
        "horizontalLtrPairs": {
            page_id: list(pair_ids) for page_id, pair_ids in sorted(horizontal_pairs.items())
        },
        "mixedOrientationPairs": {
            page_id: list(pair_ids) for page_id, pair_ids in sorted(mixed_pairs.items())
        },
        "verticalRtlControlPairs": {
            page_id: list(pair_ids) for page_id, pair_ids in sorted(vertical_pairs.items())
        },
    }


def _invalid_verdict(failures: Sequence[str]) -> VerdictResult:
    reasons = tuple(
        GateReason("harness-validity", "all", "fail", detail)
        for detail in sorted(set(failures))
    )
    return VerdictResult(
        Verdict.INVALID_EXPERIMENT,
        "INVALID",
        "NOT_EVALUATED",
        "NOT_EVALUATED",
        "NOT_EVALUATED",
        reasons,
    )


def evaluate_qualification(
    *,
    corpus_root: Path,
    diagnostics_by_arm: Mapping[ArmId, Sequence[dict[str, object]]],
    ordering_by_arm: Mapping[ArmId, Sequence[dict[str, object]]],
    repeat_hashes_by_arm: Mapping[ArmId, Sequence[Mapping[str, str]]],
    scores_by_arm: Mapping[ArmId, CorpusScore],
) -> tuple[dict[str, object], VerdictResult]:
    """Derive formal qualification state only from validated machine evidence."""
    harness_failures: list[str] = []
    corpus_valid = True
    try:
        validate_corpus(corpus_root)
    except (ContractError, OSError, json.JSONDecodeError) as error:
        corpus_valid = False
        harness_failures.append(f"corpus validation failed: {error}")

    harness_failures.extend(
        _artifact_failures(
            diagnostics_by_arm,
            ordering_by_arm,
            repeat_hashes_by_arm,
            scores_by_arm,
        )
    )

    ground_truth: dict[str, PageGroundTruth] = {}
    if corpus_valid:
        try:
            ground_truth = {
                page_id: load_ground_truth(corpus_root / "annotations" / f"{page_id}.json")
                for page_id in PAGE_IDS
            }
        except (ContractError, OSError, json.JSONDecodeError) as error:
            harness_failures.append(f"qualification declarations failed: {error}")

    required_arms = set(ArmId)
    artifacts_complete = (
        set(diagnostics_by_arm)
        == set(ordering_by_arm)
        == set(repeat_hashes_by_arm)
        == set(scores_by_arm)
        == required_arms
    )
    if artifacts_complete and ground_truth:
        a_evidence = _derive_a_exercise(
            ground_truth,
            _diag_index(diagnostics_by_arm[ArmId.A0_B0_CONTROL]),
            _diag_index(diagnostics_by_arm[ArmId.A1_B0_PANEL_ONLY]),
        )
        b_evidence = _derive_b_exercise(
            ground_truth,
            _diag_index(diagnostics_by_arm[ArmId.A0_B1_ORDER_ONLY]),
        )
    else:
        a_evidence = {"exercised": False, "qualifyingPages": []}
        b_evidence = {"exercised": False}

    harness_valid = not harness_failures
    if not artifacts_complete:
        verdict = _invalid_verdict(harness_failures or ["required arm artifacts are incomplete"])
    else:
        verdict = evaluate_verdict(
            harness_valid=harness_valid,
            control=scores_by_arm[ArmId.A0_B0_CONTROL],
            panel_only=scores_by_arm[ArmId.A1_B0_PANEL_ONLY],
            order_only=scores_by_arm[ArmId.A0_B1_ORDER_ONLY],
            combined=scores_by_arm[ArmId.A1_B1_COMBINED],
            a_exercised=a_evidence["exercised"] is True,
            b_exercised=b_evidence["exercised"] is True,
        )
    comparison = {
        "schemaVersion": COMPARISON_SCHEMA_VERSION,
        "specVersion": SPEC_VERSION,
        "requiredSlices": sorted(REQUIRED_SLICES),
        "harness": {
            "valid": harness_valid,
            "failures": sorted(set(harness_failures)),
            "corpusValidated": corpus_valid,
            "requiredArmArtifactsPresent": artifacts_complete,
            "manualQualityOverrideAccepted": False,
        },
        "exercise": {"A": a_evidence, "B": b_evidence},
    }
    return comparison, verdict
