from __future__ import annotations

import copy
import inspect
from fractions import Fraction
from pathlib import Path

import pytest
import scripts.reading_order_v2.comparison as comparison
from scripts.reading_order_v2.comparison import (
    _derive_a_exercise,
    _derive_b_exercise,
    build_repeat_hash_record,
    evaluate_qualification,
)
from scripts.reading_order_v2.contracts import (
    PAGE_IDS,
    REQUIRED_SLICES,
    PageGroundTruth,
    QualificationPair,
)
from scripts.reading_order_v2.scoring import CorpusScore, PageScore, PairMetrics
from scripts.reading_order_v2.verdict import Verdict

from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ArmId


def _metric(
    wrong: tuple[tuple[str, str], ...] = (), *, total: int = 4
) -> PairMetrics:
    inversions = len(wrong)
    return PairMetrics(
        total,
        inversions,
        total - inversions,
        Fraction(total - inversions, total),
        Fraction(inversions, total),
        wrong,
    )


def _page_score(page_id: str) -> PageScore:
    return PageScore(
        page_id,
        Fraction(1, 1),
        3,
        True,
        _metric(),
        {},
        ("r0", "r1", "r2"),
    )


def _score(*, global_wrong=(), a_wrong=(), b_wrong=()) -> CorpusScore:
    slices = {name: _metric(()) for name in REQUIRED_SLICES}
    slices["A"] = _metric(tuple(a_wrong))
    slices["B"] = _metric(tuple(b_wrong))
    return CorpusScore(
        16,
        16 - len(global_wrong),
        _metric(tuple(global_wrong)),
        slices,
        tuple(_page_score(page_id) for page_id in PAGE_IDS),
    )


def _ground_truth() -> dict[str, PageGroundTruth]:
    slice_map = {
        "H01": ("A", "partial-assignment"),
        "H02": ("B", "horizontal-only"),
        "H03": ("B", "mixed"),
        "H04": ("B", "vertical-only"),
        "H05": ("A+B",),
        "H06": ("clean-control",),
        "H07": ("intentional-fallback",),
    }
    result = {}
    for page_id in PAGE_IDS:
        slices = slice_map.get(page_id, ("clean-control",))
        pair = QualificationPair(f"{page_id}-p", "r0", "r1", slices)
        result[page_id] = PageGroundTruth(
            page_id,
            ("r0", "r1", "r2"),
            (),
            (pair,),
            (),
            (),
        )
    return result


def _region(
    region_id: str,
    *,
    status: str = "confident",
    mode: str = "b0-tier",
    orientation: str = "vertical",
    tier: str = "g000-t000",
    run: str = "g000-t000-r000",
) -> dict[str, object]:
    return {
        "regionId": region_id,
        "assignmentStatus": status,
        "localTierId": tier,
        "localRunId": run,
        "localOrderingMode": mode,
        "orientationClass": orientation,
    }


def _diagnostic(page_id: str) -> dict[str, object]:
    return {
        "pageId": page_id,
        "inputRegionCount": 3,
        "inputRegionIds": ["r0", "r1", "r2"],
        "finalOrder": ["r0", "r1", "r2"],
        "segmentation": {"reliable": True},
        "groups": [
            {"confidentRegionIds": ["r0"]},
            {"confidentRegionIds": ["r1", "r2"]},
        ],
        "regions": [_region("r0"), _region("r1"), _region("r2")],
        "usedPanelEvidence": True,
        "panelEvidenceMode": "full",
        "fallbackReason": None,
    }


def _diagnostics() -> dict[ArmId, list[dict[str, object]]]:
    result = {arm: [_diagnostic(page_id) for page_id in PAGE_IDS] for arm in ArmId}
    by_page = {
        arm: {item["pageId"]: item for item in values}
        for arm, values in result.items()
    }
    a0 = by_page[ArmId.A0_B0_CONTROL]["H01"]
    a0["usedPanelEvidence"] = False
    a0["panelEvidenceMode"] = "none"
    a0["fallbackReason"] = "region-unassigned-or-ambiguous"

    a1 = by_page[ArmId.A1_B0_PANEL_ONLY]["H01"]
    a1["usedPanelEvidence"] = True
    a1["panelEvidenceMode"] = "partial"
    a1["regions"] = [
        _region("r0"),
        _region("r1"),
        _region("r2", status="unassigned", mode="singleton"),
    ]

    b1 = by_page[ArmId.A0_B1_ORDER_ONLY]
    b1["H02"]["regions"] = [
        _region("r0", mode="ltr-horizontal", orientation="horizontal"),
        _region("r1", mode="ltr-horizontal", orientation="horizontal"),
        _region("r2", mode="ltr-horizontal", orientation="horizontal"),
    ]
    b1["H03"]["regions"] = [
        _region(
            "r0",
            mode="mixed",
            orientation="horizontal",
            run="g000-t000-r000",
        ),
        _region(
            "r1",
            mode="mixed",
            orientation="vertical",
            run="g000-t000-r001",
        ),
        _region(
            "r2",
            mode="mixed",
            orientation="vertical",
            run="g000-t000-r001",
        ),
    ]
    b1["H04"]["regions"] = [
        _region("r0", mode="rtl-vertical", orientation="vertical"),
        _region("r1", mode="rtl-vertical", orientation="vertical"),
        _region("r2", mode="rtl-vertical", orientation="vertical"),
    ]
    return result


def _ordering() -> dict[ArmId, list[dict[str, object]]]:
    return {
        arm: [
            {"pageId": page_id, "finalOrder": ["r0", "r1", "r2"]}
            for page_id in PAGE_IDS
        ]
        for arm in ArmId
    }


def _scores() -> dict[ArmId, CorpusScore]:
    return {
        ArmId.A0_B0_CONTROL: _score(
            global_wrong=(("a", "b"), ("c", "d")),
            a_wrong=(("a", "b"),),
            b_wrong=(("c", "d"),),
        ),
        ArmId.A1_B0_PANEL_ONLY: _score(
            global_wrong=(("c", "d"),),
            b_wrong=(("c", "d"),),
        ),
        ArmId.A0_B1_ORDER_ONLY: _score(
            global_wrong=(("a", "b"),),
            a_wrong=(("a", "b"),),
        ),
        ArmId.A1_B1_COMBINED: _score(),
    }


def _repeat_hashes(
    diagnostics: dict[ArmId, list[dict[str, object]]],
    ordering: dict[ArmId, list[dict[str, object]]],
    scores: dict[ArmId, CorpusScore],
) -> dict[ArmId, list[dict[str, str]]]:
    result = {}
    for arm in ArmId:
        record = build_repeat_hash_record(diagnostics[arm], ordering[arm], scores[arm])
        result[arm] = [record, dict(record), dict(record)]
    return result


def _indexed(values: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(item["pageId"]): item for item in values}


def test_valid_a_evidence_derives_a_exercised() -> None:
    diagnostics = _diagnostics()
    evidence = _derive_a_exercise(
        _ground_truth(),
        _indexed(diagnostics[ArmId.A0_B0_CONTROL]),
        _indexed(diagnostics[ArmId.A1_B0_PANEL_ONLY]),
    )
    assert evidence["exercised"] is True
    assert evidence["qualifyingPages"] == ["H01"]


@pytest.mark.parametrize(
    "condition",
    [
        "reliable-segmentation",
        "two-confident-groups",
        "uncertain-region",
        "a0-assignment-fallback",
        "a1-partial-use",
    ],
)
def test_removing_each_required_a_condition_makes_a_not_exercised(condition: str) -> None:
    diagnostics = _diagnostics()
    a0 = _indexed(diagnostics[ArmId.A0_B0_CONTROL])["H01"]
    a1 = _indexed(diagnostics[ArmId.A1_B0_PANEL_ONLY])["H01"]
    if condition == "reliable-segmentation":
        a1["segmentation"] = {"reliable": False}
    elif condition == "two-confident-groups":
        a1["groups"] = [{"confidentRegionIds": ["r0"]}, {"confidentRegionIds": []}]
    elif condition == "uncertain-region":
        a1["regions"] = [_region("r0"), _region("r1"), _region("r2")]
    elif condition == "a0-assignment-fallback":
        a0["fallbackReason"] = "precedence-cycle"
    else:
        a1["panelEvidenceMode"] = "full"
    evidence = _derive_a_exercise(_ground_truth(), {"H01": a0}, {"H01": a1})
    assert evidence["exercised"] is False


def test_valid_b_evidence_derives_b_exercised() -> None:
    diagnostics = _diagnostics()
    evidence = _derive_b_exercise(
        _ground_truth(), _indexed(diagnostics[ArmId.A0_B1_ORDER_ONLY])
    )
    assert evidence["exercised"] is True
    assert evidence["horizontalLtrPages"] == ["H02"]
    assert evidence["mixedOrientationPages"] == ["H03"]
    assert evidence["verticalRtlControlPages"] == ["H04"]
    assert evidence["horizontalLtrPairs"] == {"H02": ["H02-p"]}
    assert evidence["mixedOrientationPairs"] == {"H03": ["H03-p"]}
    assert evidence["verticalRtlControlPairs"] == {"H04": ["H04-p"]}


@pytest.mark.parametrize("page_id", ["H02", "H03", "H04"])
def test_missing_each_required_b_exercise_makes_b_not_exercised(page_id: str) -> None:
    diagnostics = _diagnostics()
    indexed = _indexed(diagnostics[ArmId.A0_B1_ORDER_ONLY])
    indexed[page_id]["usedPanelEvidence"] = False
    evidence = _derive_b_exercise(_ground_truth(), indexed)
    assert evidence["exercised"] is False


def test_normal_qualification_path_exposes_no_manual_boolean_switches() -> None:
    parameters = inspect.signature(evaluate_qualification).parameters
    assert "harness_valid" not in parameters
    assert "a_exercised" not in parameters
    assert "b_exercised" not in parameters


def test_formal_pass_only_arises_from_derived_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ground_truth = _ground_truth()
    monkeypatch.setattr(comparison, "validate_corpus", lambda root: None)
    monkeypatch.setattr(
        comparison,
        "load_ground_truth",
        lambda path: ground_truth[path.stem],
    )
    diagnostics = _diagnostics()
    ordering = _ordering()
    scores = _scores()
    repeat_hashes = _repeat_hashes(diagnostics, ordering, scores)
    payload, verdict = evaluate_qualification(
        corpus_root=tmp_path,
        diagnostics_by_arm=diagnostics,
        ordering_by_arm=ordering,
        repeat_hashes_by_arm=repeat_hashes,
        scores_by_arm=scores,
    )
    assert payload["harness"]["manualQualityOverrideAccepted"] is False
    assert verdict.verdict is Verdict.READING_ORDER_V2_HELDOUT_PASS

    changed = copy.deepcopy(diagnostics)
    a1 = _indexed(changed[ArmId.A1_B0_PANEL_ONLY])["H01"]
    a1["panelEvidenceMode"] = "full"
    changed_hashes = _repeat_hashes(changed, ordering, scores)
    _, verdict = evaluate_qualification(
        corpus_root=tmp_path,
        diagnostics_by_arm=changed,
        ordering_by_arm=ordering,
        repeat_hashes_by_arm=changed_hashes,
        scores_by_arm=scores,
    )
    assert verdict.verdict is Verdict.A_INCONCLUSIVE
