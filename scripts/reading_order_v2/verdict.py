from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ReadingOrderArm

from .scoring import ReadingOrderScores


class HarnessStatus(str, Enum):
    VALID = "VALID"
    INVALID = "INVALID"


class HypothesisStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class CombinedStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUATED = "NOT_EVALUATED"


class FormalVerdict(str, Enum):
    READING_ORDER_V2_HELDOUT_PASS = "READING_ORDER_V2_HELDOUT_PASS"
    INVALID_EXPERIMENT = "INVALID_EXPERIMENT"
    A_FAIL = "A_FAIL"
    B_FAIL = "B_FAIL"
    COMBINED_FAIL = "COMBINED_FAIL"
    A_INCONCLUSIVE = "A_INCONCLUSIVE"
    B_INCONCLUSIVE = "B_INCONCLUSIVE"


@dataclass(frozen=True, slots=True)
class ReasonRecord:
    gate: str
    arm_id: str | None
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class HarnessSignals:
    a0_matches_production: bool
    all_arms_preserve_regions: bool
    all_repeat_hashes_identical: bool
    forbidden_input_access_absent: bool


@dataclass(frozen=True, slots=True)
class ArmSignals:
    integrity_passed: bool
    deterministic: bool
    uncertainty_explicit: bool
    a_mechanism_exercised: bool = False
    b_horizontal_exercised: bool = False
    b_mixed_exercised: bool = False
    b_vertical_control_exercised: bool = False


@dataclass(frozen=True, slots=True)
class VerdictResult:
    verdict: FormalVerdict
    harness_status: HarnessStatus
    a_status: HypothesisStatus
    b_status: HypothesisStatus
    combined_status: CombinedStatus
    reasons: tuple[ReasonRecord, ...]


def _reason(
    reasons: list[ReasonRecord],
    gate: str,
    arm: ReadingOrderArm | None,
    detail: str,
) -> None:
    reasons.append(
        ReasonRecord(
            gate=gate,
            arm_id=None if arm is None else arm.value,
            status="FAIL",
            detail=detail,
        )
    )


def _slice_metric_pairs(
    scores: ReadingOrderScores,
) -> dict[str, tuple[Fraction, Fraction, frozenset[str]]]:
    return {
        item.name: (
            item.pairwise_accuracy,
            item.normalized_inversion_distance,
            frozenset(item.wrong_pairs),
        )
        for item in scores.slices
    }


def _universal_candidate_passes(
    control: ReadingOrderScores,
    candidate: ReadingOrderScores,
    signals: ArmSignals,
    arm: ReadingOrderArm,
    reasons: list[ReasonRecord],
) -> bool:
    passed = True
    control_slices = _slice_metric_pairs(control)
    candidate_slices = _slice_metric_pairs(candidate)
    if set(control_slices) != set(candidate_slices):
        _reason(reasons, "universal.slice-set", arm, "candidate/control score slice sets differ")
        return False
    for name in sorted(control_slices):
        control_accuracy, control_distance, control_wrong = control_slices[name]
        candidate_accuracy, candidate_distance, candidate_wrong = candidate_slices[name]
        if not candidate_wrong <= control_wrong:
            new_wrong = sorted(candidate_wrong - control_wrong)
            _reason(reasons, f"universal.{name}.wrong-set", arm, f"new wrong pairs: {new_wrong!r}")
            passed = False
        if candidate_accuracy < control_accuracy:
            _reason(
                reasons,
                f"universal.{name}.pairwise-accuracy",
                arm,
                "pairwise accuracy regressed",
            )
            passed = False
        if candidate_distance > control_distance:
            _reason(
                reasons,
                f"universal.{name}.normalized-inversion-distance",
                arm,
                "normalized inversion distance increased",
            )
            passed = False
    control_pages = {page.page_id: page for page in control.pages}
    candidate_pages = {page.page_id: page for page in candidate.pages}
    if set(control_pages) != set(candidate_pages):
        _reason(reasons, "universal.page-set", arm, "candidate/control page sets differ")
        passed = False
    else:
        for page_id in sorted(control_pages):
            control_wrong = set(control_pages[page_id].wrong_pairs)
            candidate_wrong = set(candidate_pages[page_id].wrong_pairs)
            if not candidate_wrong <= control_wrong:
                _reason(
                    reasons,
                    f"universal.page.{page_id}.wrong-set",
                    arm,
                    f"new wrong pairs: {sorted(candidate_wrong - control_wrong)!r}",
                )
                passed = False
    if sum(page.exact_sequence for page in candidate.pages) < sum(
        page.exact_sequence for page in control.pages
    ):
        _reason(
            reasons,
            "universal.exact-sequence-pages",
            arm,
            "exact-sequence page count decreased",
        )
        passed = False
    if not signals.integrity_passed:
        _reason(reasons, "universal.integrity", arm, "region/payload integrity gate failed")
        passed = False
    if not signals.deterministic:
        _reason(reasons, "universal.determinism", arm, "repeat hashes are not identical")
        passed = False
    if not signals.uncertainty_explicit:
        _reason(reasons, "universal.uncertainty", arm, "assignment uncertainty was not explicit")
        passed = False
    return passed


def _strict_subset(
    candidate: ReadingOrderScores, control: ReadingOrderScores, slice_name: str
) -> bool:
    return frozenset(candidate.slice(slice_name).wrong_pairs) < frozenset(
        control.slice(slice_name).wrong_pairs
    )


def _no_worse(
    candidate: ReadingOrderScores, reference: ReadingOrderScores, slice_name: str
) -> bool:
    candidate_slice = candidate.slice(slice_name)
    reference_slice = reference.slice(slice_name)
    return (
        frozenset(candidate_slice.wrong_pairs) <= frozenset(reference_slice.wrong_pairs)
        and candidate_slice.pairwise_accuracy >= reference_slice.pairwise_accuracy
        and candidate_slice.normalized_inversion_distance
        <= reference_slice.normalized_inversion_distance
    )


def evaluate_verdict(
    *,
    harness: HarnessSignals,
    scores: dict[ReadingOrderArm, ReadingOrderScores],
    signals: dict[ReadingOrderArm, ArmSignals],
) -> VerdictResult:
    required = set(ReadingOrderArm)
    if set(scores) != required or set(signals) != required:
        raise ValueError("verdict requires scores/signals for exactly the four frozen arms")
    reasons: list[ReasonRecord] = []
    harness_checks = (
        ("harness.a0-production-fidelity", harness.a0_matches_production),
        ("harness.region-preservation", harness.all_arms_preserve_regions),
        ("harness.repeat-determinism", harness.all_repeat_hashes_identical),
        ("harness.forbidden-inputs", harness.forbidden_input_access_absent),
    )
    harness_status = HarnessStatus.VALID
    for gate, passed in harness_checks:
        if not passed:
            harness_status = HarnessStatus.INVALID
            _reason(reasons, gate, None, "frozen harness-validity gate failed")

    control = scores[ReadingOrderArm.A0_B0_CONTROL]
    a_arm = ReadingOrderArm.A1_B0_PANEL_ONLY
    b_arm = ReadingOrderArm.A0_B1_ORDER_ONLY
    combined_arm = ReadingOrderArm.A1_B1_COMBINED

    a_universal = _universal_candidate_passes(
        control, scores[a_arm], signals[a_arm], a_arm, reasons
    )
    if not a_universal:
        a_status = HypothesisStatus.FAIL
    elif not signals[a_arm].a_mechanism_exercised:
        a_status = HypothesisStatus.INCONCLUSIVE
        _reason(reasons, "A.exercise", a_arm, "partial-assignment mechanism was not exercised")
    elif not _strict_subset(scores[a_arm], control, "A"):
        a_status = HypothesisStatus.INCONCLUSIVE
        _reason(reasons, "A.strict-improvement", a_arm, "A wrong-pair set is not a strict subset")
    else:
        a_status = HypothesisStatus.PASS

    b_universal = _universal_candidate_passes(
        control, scores[b_arm], signals[b_arm], b_arm, reasons
    )
    b_exercised = (
        signals[b_arm].b_horizontal_exercised
        and signals[b_arm].b_mixed_exercised
        and signals[b_arm].b_vertical_control_exercised
    )
    if not b_universal:
        b_status = HypothesisStatus.FAIL
    elif not b_exercised:
        b_status = HypothesisStatus.INCONCLUSIVE
        _reason(
            reasons,
            "B.exercise",
            b_arm,
            "required horizontal/mixed/vertical controls were not all exercised",
        )
    elif not _strict_subset(scores[b_arm], control, "B"):
        b_status = HypothesisStatus.INCONCLUSIVE
        _reason(reasons, "B.strict-improvement", b_arm, "B wrong-pair set is not a strict subset")
    elif not _no_worse(scores[b_arm], control, "vertical-only") or not _no_worse(
        scores[b_arm], control, "control"
    ):
        b_status = HypothesisStatus.FAIL
        _reason(reasons, "B.vertical-control", b_arm, "vertical-only/control pairs regressed")
    else:
        b_status = HypothesisStatus.PASS

    combined_status = CombinedStatus.NOT_EVALUATED
    if a_status is HypothesisStatus.PASS and b_status is HypothesisStatus.PASS:
        combined_universal = _universal_candidate_passes(
            control, scores[combined_arm], signals[combined_arm], combined_arm, reasons
        )
        combined_passed = combined_universal
        if not _strict_subset(scores[combined_arm], control, "aggregate"):
            _reason(
                reasons,
                "combined.strict-global-improvement",
                combined_arm,
                "global wrong-pair set is not a strict subset",
            )
            combined_passed = False
        if not _no_worse(scores[combined_arm], scores[a_arm], "A"):
            _reason(
                reasons, "combined.A-retention", combined_arm, "combined arm lost an A correction"
            )
            combined_passed = False
        if not _no_worse(scores[combined_arm], scores[b_arm], "B"):
            _reason(
                reasons, "combined.B-retention", combined_arm, "combined arm lost a B correction"
            )
            combined_passed = False
        combined_status = CombinedStatus.PASS if combined_passed else CombinedStatus.FAIL

    if harness_status is HarnessStatus.INVALID:
        verdict = FormalVerdict.INVALID_EXPERIMENT
    elif a_status is HypothesisStatus.FAIL:
        verdict = FormalVerdict.A_FAIL
    elif a_status is HypothesisStatus.INCONCLUSIVE:
        verdict = FormalVerdict.A_INCONCLUSIVE
    elif b_status is HypothesisStatus.FAIL:
        verdict = FormalVerdict.B_FAIL
    elif b_status is HypothesisStatus.INCONCLUSIVE:
        verdict = FormalVerdict.B_INCONCLUSIVE
    elif combined_status is CombinedStatus.FAIL:
        verdict = FormalVerdict.COMBINED_FAIL
    else:
        verdict = FormalVerdict.READING_ORDER_V2_HELDOUT_PASS

    return VerdictResult(
        verdict=verdict,
        harness_status=harness_status,
        a_status=a_status,
        b_status=b_status,
        combined_status=combined_status,
        reasons=tuple(reasons),
    )


def verdict_to_dict(result: VerdictResult) -> dict[str, object]:
    return {
        "schemaVersion": "reading-order-v2-verdict-v1",
        "verdict": result.verdict.value,
        "harnessStatus": result.harness_status.value,
        "aStatus": result.a_status.value,
        "bStatus": result.b_status.value,
        "combinedStatus": result.combined_status.value,
        "reasonRecords": [
            {
                "gate": reason.gate,
                "armId": reason.arm_id,
                "status": reason.status,
                "detail": reason.detail,
            }
            for reason in result.reasons
        ],
    }


def _load_json(path: "Path") -> dict[str, object]:
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object")
    return raw


def _load_diagnostics(path: "Path") -> dict[str, dict[str, object]]:
    import json

    result: dict[str, dict[str, object]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        raw = json.loads(line)
        if not isinstance(raw, dict) or not isinstance(raw.get("pageId"), str):
            raise ValueError(f"{path}:{line_number}: malformed diagnostic")
        page_id = raw["pageId"]
        if page_id in result:
            raise ValueError(f"{path}: duplicate page diagnostic {page_id}")
        result[page_id] = raw
    return result


def _diagnostic_integrity(pages: dict[str, dict[str, object]]) -> bool:
    for page in pages.values():
        input_ids = page.get("inputRegionIds")
        final_order = page.get("finalOrder")
        regions = page.get("regions")
        if (
            not isinstance(input_ids, list)
            or not all(isinstance(value, str) for value in input_ids)
            or not isinstance(final_order, list)
            or not all(isinstance(value, str) for value in final_order)
            or not isinstance(regions, list)
        ):
            return False
        if len(input_ids) != len(set(input_ids)) or len(final_order) != len(set(final_order)):
            return False
        if set(input_ids) != set(final_order) or len(input_ids) != len(final_order):
            return False
        if page.get("inputRegionCount") != len(input_ids):
            return False
        region_ids = [
            region.get("regionId")
            for region in regions
            if isinstance(region, dict) and isinstance(region.get("regionId"), str)
        ]
        if len(region_ids) != len(regions) or set(region_ids) != set(input_ids):
            return False
    return True


def _diagnostic_uncertainty_explicit(pages: dict[str, dict[str, object]]) -> bool:
    for page in pages.values():
        regions = page.get("regions")
        if not isinstance(regions, list):
            return False
        for region in regions:
            if not isinstance(region, dict):
                return False
            candidates = region.get("candidateGroupIds")
            status = region.get("assignmentStatus")
            assigned = region.get("assignedGroupId")
            if not isinstance(candidates, list):
                return False
            if len(candidates) == 0:
                if status != "unassigned" or assigned is not None:
                    return False
            elif len(candidates) == 1:
                if status != "confident":
                    return False
            else:
                if status != "ambiguous" or assigned is not None:
                    return False
    return True


def _repeat_hashes_identical(path: "Path") -> bool:
    raw = _load_json(path)
    repeats = raw.get("repeats")
    if not isinstance(repeats, dict) or tuple(sorted(repeats)) != (
        "repeat-1",
        "repeat-2",
        "repeat-3",
    ):
        return False
    records = [repeats[name] for name in sorted(repeats)]
    if not all(isinstance(record, dict) for record in records):
        return False
    for key in ("diagnosticsSha256", "orderingSha256", "scoresSha256"):
        values = [record.get(key) for record in records if isinstance(record, dict)]
        if len(values) != 3 or len(set(values)) != 1:
            return False
    return True


def _page_has_pair_slice(annotation: object, required: set[str]) -> bool:
    from .contracts import AnnotationPage

    if not isinstance(annotation, AnnotationPage):
        return False
    return any(required <= set(pair.slices) for pair in annotation.qualification_pairs)


def derive_signals_from_outputs(
    *,
    arm: ReadingOrderArm,
    diagnostics: dict[str, dict[str, object]],
    repeat_hashes_path: "Path",
    annotations: tuple[object, ...],
    control_diagnostics: dict[str, dict[str, object]],
) -> ArmSignals:
    deterministic = _repeat_hashes_identical(repeat_hashes_path)
    integrity = _diagnostic_integrity(diagnostics)
    uncertainty = _diagnostic_uncertainty_explicit(diagnostics)

    a_exercised = False
    if arm.uses_partial_assignment:
        for page_id, page in diagnostics.items():
            control = control_diagnostics.get(page_id)
            groups = page.get("groups")
            regions = page.get("regions")
            segmentation = page.get("segmentation")
            confident_groups = (
                sum(
                    bool(group.get("confidentRegionIds"))
                    for group in groups
                    if isinstance(group, dict)
                )
                if isinstance(groups, list)
                else 0
            )
            uncertain = (
                any(
                    region.get("assignmentStatus") in {"unassigned", "ambiguous"}
                    for region in regions
                    if isinstance(region, dict)
                )
                if isinstance(regions, list)
                else False
            )
            segmentation_reliable = (
                isinstance(segmentation, dict) and segmentation.get("reliable") is True
            )
            if (
                page.get("panelEvidenceMode") == "partial"
                and segmentation_reliable
                and confident_groups >= 2
                and uncertain
                and isinstance(control, dict)
                and control.get("fallbackReason") == "region-unassigned-or-ambiguous"
            ):
                a_exercised = True
                break

    b_pages = {
        annotation.page_id
        for annotation in annotations
        if _page_has_pair_slice(annotation, {"B"})
    }
    relevant_b_uses_panels = bool(b_pages) and all(
        diagnostics.get(page_id, {}).get("usedPanelEvidence") is True for page_id in b_pages
    )
    horizontal_pages = {
        annotation.page_id
        for annotation in annotations
        if _page_has_pair_slice(annotation, {"B", "horizontal-only"})
    }
    mixed_pages = {
        annotation.page_id
        for annotation in annotations
        if _page_has_pair_slice(annotation, {"B", "mixed"})
    }
    vertical_control_pages = {
        annotation.page_id
        for annotation in annotations
        if _page_has_pair_slice(annotation, {"control", "vertical-only"})
    }
    horizontal_exercised = relevant_b_uses_panels and any(
        diagnostics.get(page_id, {}).get("usedPanelEvidence") is True
        for page_id in horizontal_pages
    )
    mixed_exercised = relevant_b_uses_panels and any(
        diagnostics.get(page_id, {}).get("usedPanelEvidence") is True for page_id in mixed_pages
    )
    vertical_exercised = bool(vertical_control_pages) and all(
        diagnostics.get(page_id, {}).get("usedPanelEvidence") is True
        for page_id in vertical_control_pages
    )
    return ArmSignals(
        integrity_passed=integrity,
        deterministic=deterministic,
        uncertainty_explicit=uncertainty,
        a_mechanism_exercised=a_exercised,
        b_horizontal_exercised=horizontal_exercised,
        b_mixed_exercised=mixed_exercised,
        b_vertical_control_exercised=vertical_exercised,
    )


def comparison_to_dict(scores: dict[ReadingOrderArm, ReadingOrderScores]) -> dict[str, object]:
    from .scoring import wrong_set_comparison

    control = scores[ReadingOrderArm.A0_B0_CONTROL]
    candidates: dict[str, object] = {}
    for arm in (
        ReadingOrderArm.A1_B0_PANEL_ONLY,
        ReadingOrderArm.A0_B1_ORDER_ONLY,
        ReadingOrderArm.A1_B1_COMBINED,
    ):
        candidate = scores[arm]
        slice_comparisons: dict[str, object] = {}
        for control_slice in control.slices:
            candidate_slice = candidate.slice(control_slice.name)
            control_wrong = frozenset(control_slice.wrong_pairs)
            candidate_wrong = frozenset(candidate_slice.wrong_pairs)
            slice_comparisons[control_slice.name] = {
                "candidateWrongPairSubsetOfControl": candidate_wrong <= control_wrong,
                "candidateWrongPairStrictSubsetOfControl": candidate_wrong < control_wrong,
                "newWrongPairs": sorted(candidate_wrong - control_wrong),
                "correctedWrongPairs": sorted(control_wrong - candidate_wrong),
            }
        candidates[arm.value] = {
            "aggregate": wrong_set_comparison(control, candidate),
            "slices": slice_comparisons,
        }
    return {
        "schemaVersion": "reading-order-v2-comparison-v1",
        "controlArmId": ReadingOrderArm.A0_B0_CONTROL.value,
        "candidates": candidates,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    from .canonical import write_canonical_json
    from .contracts import PAGE_IDS, load_annotation
    from .scoring import load_scores

    parser = argparse.ArgumentParser(
        description="Compute frozen Reading Order v2 comparison/verdict"
    )
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    annotations = tuple(
        load_annotation(args.corpus_root / "annotations" / f"{page_id}.json")
        for page_id in PAGE_IDS
    )
    scores = {
        arm: load_scores(args.output_root / "arms" / arm.value / "scores.json")
        for arm in ReadingOrderArm
    }
    diagnostics = {
        arm: _load_diagnostics(args.output_root / "arms" / arm.value / "diagnostics.jsonl")
        for arm in ReadingOrderArm
    }
    control_diag = diagnostics[ReadingOrderArm.A0_B0_CONTROL]
    signals = {
        arm: derive_signals_from_outputs(
            arm=arm,
            diagnostics=diagnostics[arm],
            repeat_hashes_path=args.output_root / "arms" / arm.value / "repeat-hashes.json",
            annotations=annotations,
            control_diagnostics=control_diag,
        )
        for arm in ReadingOrderArm
    }
    control_ordering = _load_json(
        args.output_root / "arms" / ReadingOrderArm.A0_B0_CONTROL.value / "ordering.json"
    )
    harness = HarnessSignals(
        a0_matches_production=control_ordering.get("productionFidelityVerified") is True,
        all_arms_preserve_regions=all(signal.integrity_passed for signal in signals.values()),
        all_repeat_hashes_identical=all(signal.deterministic for signal in signals.values()),
        forbidden_input_access_absent=True,
    )
    result = evaluate_verdict(harness=harness, scores=scores, signals=signals)
    write_canonical_json(args.output_root / "comparison.json", comparison_to_dict(scores))
    write_canonical_json(args.output_root / "verdict.json", verdict_to_dict(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
