from __future__ import annotations

import pytest

from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ReadingOrderArm
from scripts.reading_order_v2.scoring import ReadingOrderScores, SliceScore
from scripts.reading_order_v2.verdict import (
    ArmSignals,
    CombinedStatus,
    FormalVerdict,
    HarnessSignals,
    HypothesisStatus,
    evaluate_verdict,
)


def scores(wrong: set[str]) -> ReadingOrderScores:
    names = (
        "aggregate",
        "A",
        "B",
        "A+B",
        "control",
        "clean-control",
        "vertical-only",
        "horizontal-only",
        "mixed",
        "partial-assignment",
        "intentional-fallback",
    )
    slices = tuple(
        SliceScore(name, max(1, len(wrong)), len(wrong), tuple(sorted(wrong)))
        for name in names
    )
    return ReadingOrderScores((), slices)


def signals(**kwargs: object) -> ArmSignals:
    defaults: dict[str, object] = dict(
        integrity_passed=True,
        deterministic=True,
        uncertainty_explicit=True,
        a_mechanism_exercised=True,
        b_horizontal_exercised=True,
        b_mixed_exercised=True,
        b_vertical_control_exercised=True,
    )
    defaults.update(kwargs)
    return ArmSignals(**defaults)  # type: ignore[arg-type]


def bundle(
    control_wrong: set[str],
    a_wrong: set[str],
    b_wrong: set[str],
    combined_wrong: set[str],
) -> dict[ReadingOrderArm, ReadingOrderScores]:
    return {
        ReadingOrderArm.A0_B0_CONTROL: scores(control_wrong),
        ReadingOrderArm.A1_B0_PANEL_ONLY: scores(a_wrong),
        ReadingOrderArm.A0_B1_ORDER_ONLY: scores(b_wrong),
        ReadingOrderArm.A1_B1_COMBINED: scores(combined_wrong),
    }


def signal_bundle(
    **overrides: ArmSignals,
) -> dict[ReadingOrderArm, ArmSignals]:
    result = {arm: signals() for arm in ReadingOrderArm}
    for key, value in overrides.items():
        result[ReadingOrderArm(key)] = value
    return result


def harness(**overrides: bool) -> HarnessSignals:
    values = dict(
        a0_matches_production=True,
        all_arms_preserve_regions=True,
        all_repeat_hashes_identical=True,
        forbidden_input_access_absent=True,
    )
    values.update(overrides)
    return HarnessSignals(**values)


def test_formal_pass_requires_a_b_and_combined_strict_progress() -> None:
    result = evaluate_verdict(
        harness=harness(),
        scores=bundle({"x", "y", "z"}, {"y", "z"}, {"x", "z"}, {"z"}),
        signals={arm: signals() for arm in ReadingOrderArm},
    )
    assert result.verdict is FormalVerdict.READING_ORDER_V2_HELDOUT_PASS
    assert result.a_status is HypothesisStatus.PASS
    assert result.b_status is HypothesisStatus.PASS
    assert result.combined_status is CombinedStatus.PASS


def test_invalid_experiment_precedes_quality_outcomes() -> None:
    result = evaluate_verdict(
        harness=harness(a0_matches_production=False),
        scores=bundle({"x"}, {"new"}, {"new"}, {"new"}),
        signals={arm: signals() for arm in ReadingOrderArm},
    )
    assert result.verdict is FormalVerdict.INVALID_EXPERIMENT
    assert any(
        reason.gate == "harness.a0-production-fidelity" for reason in result.reasons
    )


def test_a_failure_and_a_inconclusive_are_distinct() -> None:
    failed = evaluate_verdict(
        harness=harness(),
        scores=bundle({"x"}, {"new"}, set(), set()),
        signals={arm: signals() for arm in ReadingOrderArm},
    )
    assert failed.verdict is FormalVerdict.A_FAIL
    inconclusive = evaluate_verdict(
        harness=harness(),
        scores=bundle({"x"}, {"x"}, set(), set()),
        signals={arm: signals() for arm in ReadingOrderArm},
    )
    assert inconclusive.verdict is FormalVerdict.A_INCONCLUSIVE


def test_b_failure_and_b_inconclusive_are_distinct() -> None:
    failed = evaluate_verdict(
        harness=harness(),
        scores=bundle({"x", "y"}, {"y"}, {"new"}, set()),
        signals={arm: signals() for arm in ReadingOrderArm},
    )
    assert failed.verdict is FormalVerdict.B_FAIL
    inconclusive = evaluate_verdict(
        harness=harness(),
        scores=bundle({"x", "y"}, {"y"}, {"x", "y"}, set()),
        signals={arm: signals() for arm in ReadingOrderArm},
    )
    assert inconclusive.verdict is FormalVerdict.B_INCONCLUSIVE


def test_combined_failure_is_reported_after_a_and_b_pass() -> None:
    result = evaluate_verdict(
        harness=harness(),
        scores=bundle({"x", "y", "z"}, {"y", "z"}, {"x", "z"}, {"x", "y"}),
        signals={arm: signals() for arm in ReadingOrderArm},
    )
    assert result.verdict is FormalVerdict.COMBINED_FAIL
    assert result.combined_status is CombinedStatus.FAIL


def test_missing_frozen_arm_is_rejected() -> None:
    score_map = bundle({"x"}, set(), set(), set())
    del score_map[ReadingOrderArm.A1_B1_COMBINED]
    with pytest.raises(ValueError, match="exactly the four frozen arms"):
        evaluate_verdict(
            harness=harness(),
            scores=score_map,
            signals={arm: signals() for arm in ReadingOrderArm},
        )
