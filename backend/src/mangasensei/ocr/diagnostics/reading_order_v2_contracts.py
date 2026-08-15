"""Typed contracts for the Reading Order v2 research experiment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = "reading-order-v2-diagnostic-v1"
SPEC_VERSION = "reading-order-v2-experiment-spec-v1"


class ArmId(StrEnum):
    A0_B0_CONTROL = "A0_B0_CONTROL"
    A1_B0_PANEL_ONLY = "A1_B0_PANEL_ONLY"
    A0_B1_ORDER_ONLY = "A0_B1_ORDER_ONLY"
    A1_B1_COMBINED = "A1_B1_COMBINED"

    @property
    def partial_panel_evidence(self) -> bool:
        return self in {self.A1_B0_PANEL_ONLY, self.A1_B1_COMBINED}

    @property
    def orientation_aware_local_order(self) -> bool:
        return self in {self.A0_B1_ORDER_ONLY, self.A1_B1_COMBINED}


class OrientationClass(StrEnum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    AMBIGUOUS = "ambiguous"


class AssignmentStatus(StrEnum):
    CONFIDENT = "confident"
    UNASSIGNED = "unassigned"
    AMBIGUOUS = "ambiguous"


class PanelEvidenceMode(StrEnum):
    NONE = "none"
    FULL = "full"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class ExperimentRegion:
    """Corpus-owned stable identity around a real post-merge TextBlock object."""

    region_id: str
    source_index: int
    region: Any


@dataclass(frozen=True, slots=True)
class RationalDiagnostic:
    numerator: int
    denominator: int


@dataclass(frozen=True, slots=True)
class PrecedenceEdgeDiagnostic:
    target_group_id: str
    rule: str
    x_overlap: RationalDiagnostic
    y_overlap: RationalDiagnostic


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
    tie_key: tuple[object, ...]
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
    assignment_reason: str
    ambiguity_count: int
    assigned_group_id: str | None
    fallback_index: int
    local_tier_id: str | None
    local_run_id: str | None
    local_ordering_mode: str
    local_ordering_key: tuple[object, ...]
    final_page_index: int


@dataclass(frozen=True, slots=True)
class SegmentationDiagnostic:
    attempted: bool
    reliable: bool
    reason: str
    detected_group_count: int


@dataclass(frozen=True, slots=True)
class PageDiagnostic:
    schema_version: str
    spec_version: str
    repository_sha: str
    arm_id: ArmId
    page_id: str
    input_region_count: int
    input_region_ids: tuple[str, ...]
    fallback_order: tuple[str, ...]
    segmentation: SegmentationDiagnostic
    assignment_policy: str
    panel_evidence_mode: PanelEvidenceMode
    used_panel_evidence: bool
    fallback_reason: str | None
    final_order: tuple[str, ...]
    groups: tuple[GroupDiagnostic, ...]
    regions: tuple[RegionDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class ArmResult:
    ordered_regions: tuple[ExperimentRegion, ...]
    diagnostic: PageDiagnostic


def diagnostic_to_dict(value: PageDiagnostic) -> dict[str, object]:
    """Serialize the frozen diagnostic schema without exposing runtime object identity."""
    return {
        "schemaVersion": value.schema_version,
        "specVersion": value.spec_version,
        "repositorySha": value.repository_sha,
        "armId": value.arm_id.value,
        "pageId": value.page_id,
        "inputRegionCount": value.input_region_count,
        "inputRegionIds": list(value.input_region_ids),
        "fallbackOrder": list(value.fallback_order),
        "segmentation": {
            "attempted": value.segmentation.attempted,
            "reliable": value.segmentation.reliable,
            "reason": value.segmentation.reason,
            "detectedGroupCount": value.segmentation.detected_group_count,
        },
        "assignmentPolicy": value.assignment_policy,
        "panelEvidenceMode": value.panel_evidence_mode.value,
        "usedPanelEvidence": value.used_panel_evidence,
        "fallbackReason": value.fallback_reason,
        "finalOrder": list(value.final_order),
        "groups": [
            {
                "groupId": group.group_id,
                "sourceGroupIndex": group.source_group_index,
                "bbox": list(group.bbox),
                "polygon": None,
                "center2x": group.center2x,
                "center2y": group.center2y,
                "candidateRegionIds": list(group.candidate_region_ids),
                "confidentRegionIds": list(group.confident_region_ids),
                "fallbackRank": group.fallback_rank,
                "precedenceIndex": group.precedence_index,
                "tieKey": list(group.tie_key),
                "precedenceEdges": [
                    {
                        "targetGroupId": edge.target_group_id,
                        "rule": edge.rule,
                        "xOverlap": {
                            "numerator": edge.x_overlap.numerator,
                            "denominator": edge.x_overlap.denominator,
                        },
                        "yOverlap": {
                            "numerator": edge.y_overlap.numerator,
                            "denominator": edge.y_overlap.denominator,
                        },
                    }
                    for edge in group.precedence_edges
                ],
            }
            for group in value.groups
        ],
        "regions": [
            {
                "regionId": region.region_id,
                "sourceIndex": region.source_index,
                "bbox": list(region.bbox),
                "polygon": [list(point) for point in region.polygon],
                "center2x": region.center2x,
                "center2y": region.center2y,
                "directionRaw": region.direction_raw,
                "orientationClass": region.orientation_class.value,
                "candidateGroupIds": list(region.candidate_group_ids),
                "assignmentStatus": region.assignment_status.value,
                "assignmentReason": region.assignment_reason,
                "ambiguityCount": region.ambiguity_count,
                "assignedGroupId": region.assigned_group_id,
                "fallbackIndex": region.fallback_index,
                "localTierId": region.local_tier_id,
                "localRunId": region.local_run_id,
                "localOrderingMode": region.local_ordering_mode,
                "localOrderingKey": list(region.local_ordering_key),
                "finalPageIndex": region.final_page_index,
            }
            for region in value.regions
        ],
    }
