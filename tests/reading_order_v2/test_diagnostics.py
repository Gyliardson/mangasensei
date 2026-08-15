from __future__ import annotations

from fractions import Fraction

from mangasensei.ocr.diagnostics.reading_order_v2 import diagnostic_to_dict
from mangasensei.ocr.diagnostics.reading_order_v2_contracts import (
    AssignmentReason,
    AssignmentStatus,
    GroupDiagnostic,
    LocalOrderingMode,
    OrientationClass,
    PageDiagnostic,
    PanelEvidenceMode,
    PrecedenceEdgeDiagnostic,
    RegionDiagnostic,
    SegmentationDiagnostic,
)


def test_diagnostic_serializes_exact_precedence_edge_and_integer_centers() -> None:
    edge = PrecedenceEdgeDiagnostic(
        "g001",
        "same-level-right-before-left",
        Fraction(0, 1),
        Fraction(1, 1),
    )
    group = GroupDiagnostic(
        "g000",
        0,
        (0, 0, 100, 100),
        None,
        100,
        100,
        ("r0",),
        ("r0",),
        0,
        0,
        (0, 0, -100, 0),
        (edge,),
    )
    region = RegionDiagnostic(
        "r0",
        0,
        (0, 0, 10, 20),
        ((0, 0), (10, 0), (10, 20), (0, 20)),
        10,
        20,
        "h",
        OrientationClass.HORIZONTAL,
        ("g000",),
        AssignmentStatus.CONFIDENT,
        AssignmentReason.UNIQUE,
        1,
        "g000",
        0,
        "g000-t000",
        "g000-r000",
        LocalOrderingMode.B0_TIER,
        (-5, 0, 0),
        0,
    )
    page = PageDiagnostic(
        "reading-order-v2-diagnostic-v1",
        "reading-order-v2-experiment-spec-v1",
        "0" * 40,
        "A0_B0_CONTROL",
        "fixture",
        1,
        ("r0",),
        ("r0",),
        SegmentationDiagnostic(True, True, "reliable", 1),
        "current-strict-all-or-fallback",
        "reliable",
        PanelEvidenceMode.FULL,
        True,
        None,
        ("r0",),
        (group,),
        (region,),
    )
    data = diagnostic_to_dict(page)
    assert data["groups"][0]["polygon"] is None
    assert data["groups"][0]["center2x"] == 100
    assert data["groups"][0]["precedenceEdges"] == [
        {
            "targetGroupId": "g001",
            "rule": "same-level-right-before-left",
            "xOverlapNumerator": 0,
            "xOverlapDenominator": 1,
            "yOverlapNumerator": 1,
            "yOverlapDenominator": 1,
        }
    ]
    assert data["regions"][0]["center2y"] == 20
    assert "objectId" not in repr(data)
