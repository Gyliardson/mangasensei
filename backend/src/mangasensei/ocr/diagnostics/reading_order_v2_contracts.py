"""Typed contracts for the frozen Reading Order v2 research harness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

DIAGNOSTIC_SCHEMA_VERSION = "reading-order-v2-diagnostic-v1"
SPEC_VERSION = "reading-order-v2-experiment-spec-v1"
BASELINE_REPOSITORY_SHA = "292f0a8c8142d919ac4184159d102789c43b4116"


class ReadingOrderArm(str, Enum):
    A0_B0_CONTROL = "A0_B0_CONTROL"
    A1_B0_PANEL_ONLY = "A1_B0_PANEL_ONLY"
    A0_B1_ORDER_ONLY = "A0_B1_ORDER_ONLY"
    A1_B1_COMBINED = "A1_B1_COMBINED"

    @property
    def uses_partial_assignment(self) -> bool:
        return self in {self.A1_B0_PANEL_ONLY, self.A1_B1_COMBINED}

    @property
    def uses_orientation_order(self) -> bool:
        return self in {self.A0_B1_ORDER_ONLY, self.A1_B1_COMBINED}


class OrientationClass(str, Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    AMBIGUOUS = "ambiguous"


class AssignmentStatus(str, Enum):
    CONFIDENT = "confident"
    UNASSIGNED = "unassigned"
    AMBIGUOUS = "ambiguous"


class AssignmentReason(str, Enum):
    UNIQUE_CENTER = "unique-center-containment"
    NO_CENTER = "no-center-containment"
    MULTIPLE_CENTER = "multiple-center-containment"


class PanelEvidenceMode(str, Enum):
    NONE = "none"
    FULL = "full"
    PARTIAL = "partial"


class AssignmentPolicy(str, Enum):
    A0_STRICT = "current-strict-all-or-fallback"
    A1_PARTIAL = "v2-partial-explicit-uncertainty"


class LocalOrderingMode(str, Enum):
    B0_TIER = "b0-tier"
    LTR_HORIZONTAL = "ltr-horizontal"
    RTL_VERTICAL = "rtl-vertical"
    MIXED = "mixed"
    SINGLETON = "singleton"


@dataclass(frozen=True, slots=True)
class ExperimentRegion:
    """Stable experiment identity paired with the actual post-merge region object."""

    region_id: str
    source_index: int
    region: Any


@dataclass(frozen=True, slots=True)
class LocalOrderTrace:
    tier_id: str | None
    run_id: str | None
    mode: LocalOrderingMode
    key: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PrecedenceEdgeDiagnostic:
    target_group_id: str
    rule: str
    x_overlap_numerator: int
    x_overlap_denominator: int
    y_overlap_numerator: int
    y_overlap_denominator: int


@dataclass(frozen=True, slots=True)
class GroupDiagnostic:
    group_id: str
    source_group_index: int
    bbox: tuple[int, int, int, int]
    center2x: int
    center2y: int
    candidate_region_ids: tuple[str, ...]
    confident_region_ids: tuple[str, ...]
    fallback_rank: int | None
    precedence_index: int | None
    tie_key: tuple[int | None, int, int, int]
    precedence_edges: tuple[PrecedenceEdgeDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class RegionDiagnostic:
    region_id: str
    source_index: int
    bbox: tuple[int, int, int, int]
    polygon: tuple[tuple[int, int], ...]
    center2x: int
    center2y: int
    direction_raw: str
    orientation_class: OrientationClass
    candidate_group_ids: tuple[str, ...]
    assignment_status: AssignmentStatus
    assignment_reason: AssignmentReason
    ambiguity_count: int
    assigned_group_id: str | None
    fallback_index: int
    local_tier_id: str | None
    local_run_id: str | None
    local_ordering_mode: LocalOrderingMode
    local_ordering_key: tuple[int, ...]
    final_page_index: int


@dataclass(frozen=True, slots=True)
class SegmentationDiagnostic:
    attempted: bool
    reliable: bool | None
    reason: str
    detected_group_count: int


@dataclass(frozen=True, slots=True)
class PageDiagnostic:
    schema_version: str
    spec_version: str
    repository_sha: str
    arm_id: ReadingOrderArm
    page_id: str
    input_region_count: int
    input_region_ids: tuple[str, ...]
    fallback_order: tuple[str, ...]
    segmentation: SegmentationDiagnostic
    assignment_policy: AssignmentPolicy
    raw_assignment_reason: str | None
    panel_evidence_mode: PanelEvidenceMode
    used_panel_evidence: bool
    fallback_reason: str | None
    final_order: tuple[str, ...]
    groups: tuple[GroupDiagnostic, ...]
    regions: tuple[RegionDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class ReadingOrderV2Result:
    regions: tuple[Any, ...]
    diagnostic: PageDiagnostic
