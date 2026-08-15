from __future__ import annotations

import copy

import pytest
from scripts.reading_order_v2.comparison import _derive_b_exercise
from scripts.reading_order_v2.contracts import PageGroundTruth, QualificationPair


def _page(page_id: str, slices: tuple[str, ...]) -> PageGroundTruth:
    return PageGroundTruth(
        page_id,
        ("r0", "r1", "r2"),
        (),
        (QualificationPair(f"{page_id}-q", "r0", "r1", slices),),
        (),
        (),
    )


def _ground_truth() -> dict[str, PageGroundTruth]:
    return {
        "H01": _page("H01", ("B", "horizontal-only")),
        "H02": _page("H02", ("B", "mixed")),
        "H03": _page("H03", ("vertical-only",)),
    }


def _region(
    region_id: str,
    *,
    tier: str,
    run: str,
    mode: str,
    orientation: str,
) -> dict[str, object]:
    return {
        "regionId": region_id,
        "localTierId": tier,
        "localRunId": run,
        "localOrderingMode": mode,
        "orientationClass": orientation,
    }


def _diagnostics() -> dict[str, dict[str, object]]:
    return {
        "H01": {
            "usedPanelEvidence": True,
            "regions": [
                _region(
                    "r0",
                    tier="g000-t000",
                    run="g000-t000-r000",
                    mode="ltr-horizontal",
                    orientation="horizontal",
                ),
                _region(
                    "r1",
                    tier="g000-t000",
                    run="g000-t000-r000",
                    mode="ltr-horizontal",
                    orientation="horizontal",
                ),
                _region(
                    "r2",
                    tier="g000-t001",
                    run="g000-t001-r000",
                    mode="rtl-vertical",
                    orientation="vertical",
                ),
            ],
        },
        "H02": {
            "usedPanelEvidence": True,
            "regions": [
                _region(
                    "r0",
                    tier="g000-t000",
                    run="g000-t000-r000",
                    mode="mixed",
                    orientation="horizontal",
                ),
                _region(
                    "r1",
                    tier="g000-t000",
                    run="g000-t000-r001",
                    mode="mixed",
                    orientation="vertical",
                ),
                _region(
                    "r2",
                    tier="g000-t001",
                    run="g000-t001-r000",
                    mode="ltr-horizontal",
                    orientation="horizontal",
                ),
            ],
        },
        "H03": {
            "usedPanelEvidence": True,
            "regions": [
                _region(
                    "r0",
                    tier="g000-t000",
                    run="g000-t000-r000",
                    mode="rtl-vertical",
                    orientation="vertical",
                ),
                _region(
                    "r1",
                    tier="g000-t000",
                    run="g000-t000-r000",
                    mode="rtl-vertical",
                    orientation="vertical",
                ),
                _region(
                    "r2",
                    tier="g000-t001",
                    run="g000-t001-r000",
                    mode="ltr-horizontal",
                    orientation="horizontal",
                ),
            ],
        },
    }


def test_b_exercise_is_bound_to_declared_qualification_pairs() -> None:
    evidence = _derive_b_exercise(_ground_truth(), _diagnostics())

    assert evidence["exercised"] is True
    assert evidence["horizontalLtrPairs"] == {"H01": ["H01-q"]}
    assert evidence["mixedOrientationPairs"] == {"H02": ["H02-q"]}
    assert evidence["verticalRtlControlPairs"] == {"H03": ["H03-q"]}


def test_unrelated_horizontal_mode_does_not_satisfy_declared_pair() -> None:
    diagnostics = _diagnostics()
    horizontal = diagnostics["H01"]["regions"]
    assert isinstance(horizontal, list)
    horizontal[0] = _region(
        "r0",
        tier="g000-t000",
        run="g000-t000-r000",
        mode="b0-tier",
        orientation="horizontal",
    )
    horizontal[1] = _region(
        "r1",
        tier="g000-t001",
        run="g000-t001-r000",
        mode="b0-tier",
        orientation="horizontal",
    )
    horizontal[2] = _region(
        "r2",
        tier="g000-t002",
        run="g000-t002-r000",
        mode="ltr-horizontal",
        orientation="horizontal",
    )

    evidence = _derive_b_exercise(_ground_truth(), diagnostics)
    assert evidence["exercised"] is False
    assert evidence["horizontalLtrPages"] == []
    assert evidence["horizontalLtrPairs"] == {}


@pytest.mark.parametrize("field", ["localTierId", "localRunId"])
def test_horizontal_pair_must_share_tier_and_run(field: str) -> None:
    diagnostics = _diagnostics()
    regions = diagnostics["H01"]["regions"]
    assert isinstance(regions, list)
    assert isinstance(regions[1], dict)
    regions[1][field] = "different"

    evidence = _derive_b_exercise(_ground_truth(), diagnostics)
    assert evidence["exercised"] is False
    assert evidence["horizontalLtrPages"] == []


def test_mixed_pair_requires_opposite_orientations_in_same_tier_and_distinct_runs() -> None:
    diagnostics = _diagnostics()
    regions = diagnostics["H02"]["regions"]
    assert isinstance(regions, list)
    assert isinstance(regions[1], dict)
    regions[1]["orientationClass"] = "horizontal"
    regions[2] = _region(
        "r2",
        tier="g000-t000",
        run="g000-t000-r002",
        mode="mixed",
        orientation="vertical",
    )

    evidence = _derive_b_exercise(_ground_truth(), diagnostics)
    assert evidence["exercised"] is False
    assert evidence["mixedOrientationPages"] == []


def test_mixed_pair_on_different_tiers_does_not_count() -> None:
    diagnostics = _diagnostics()
    regions = diagnostics["H02"]["regions"]
    assert isinstance(regions, list)
    assert isinstance(regions[1], dict)
    regions[1]["localTierId"] = "g000-t001"

    evidence = _derive_b_exercise(_ground_truth(), diagnostics)
    assert evidence["exercised"] is False
    assert evidence["mixedOrientationPages"] == []


@pytest.mark.parametrize("field", ["localTierId", "localRunId"])
def test_vertical_control_pair_must_share_tier_and_run(field: str) -> None:
    diagnostics = copy.deepcopy(_diagnostics())
    regions = diagnostics["H03"]["regions"]
    assert isinstance(regions, list)
    assert isinstance(regions[1], dict)
    regions[1][field] = "different"

    evidence = _derive_b_exercise(_ground_truth(), diagnostics)
    assert evidence["exercised"] is False
    assert evidence["verticalRtlControlPages"] == []


def test_pair_evidence_requires_panel_evidence() -> None:
    diagnostics = _diagnostics()
    diagnostics["H01"]["usedPanelEvidence"] = False

    evidence = _derive_b_exercise(_ground_truth(), diagnostics)
    assert evidence["exercised"] is False
    assert evidence["horizontalLtrPages"] == []
