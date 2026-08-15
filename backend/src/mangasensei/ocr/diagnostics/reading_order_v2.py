"""Frozen Reading Order v2 experimental arms and geometry-only diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from mangasensei.ocr.reading_order import (
    PanelAssignment,
    PanelBox,
    PanelSegmentation,
    _candidate_group_indices,
    _deterministic_topological_order,
    _materialize_panel_flow_v1,
    _panel_precedence_edges,
    _partition_manga_tiers,
    _resolve_panel_flow_v1,
    manga_tier_order,
    segment_panel_groups,
)

from .reading_order_v2_contracts import (
    SCHEMA_VERSION,
    SPEC_VERSION,
    ArmId,
    ArmResult,
    AssignmentStatus,
    ExperimentRegion,
    GroupDiagnostic,
    OrientationClass,
    PageDiagnostic,
    PanelEvidenceMode,
    PrecedenceEdgeDiagnostic,
    RationalDiagnostic,
    RegionDiagnostic,
    SegmentationDiagnostic,
)


@dataclass(frozen=True, slots=True)
class _AssignmentObservation:
    candidates: tuple[int, ...]
    status: AssignmentStatus
    reason: str
    assigned_group: int | None


@dataclass(frozen=True, slots=True)
class _A1Resolution:
    fallback: tuple[Any, ...]
    segmentation: PanelSegmentation | None
    observations: tuple[_AssignmentObservation, ...]
    groups: tuple[tuple[int, ...], ...]
    fallback_ranks: tuple[int | None, ...]
    precedence_edges: tuple[Any, ...]
    node_order: tuple[int, ...] | None
    uncertain_region_indices: tuple[int, ...]
    fallback_reason: str | None
    panel_evidence_mode: PanelEvidenceMode
    used_panel_evidence: bool


def _normalize_orientation(value: object) -> OrientationClass:
    direction = str(value or "")
    if direction in {"h", "hr"}:
        return OrientationClass.HORIZONTAL
    if direction in {"v", "vr"}:
        return OrientationClass.VERTICAL
    return OrientationClass.AMBIGUOUS


def _raw_geometry(region: Any) -> tuple[int, int, int, int, int, int]:
    x1, y1, x2, y2 = (int(value) for value in region.xyxy)
    return x1, y1, x2, y2, x1 + x2, y1 + y2


def _build_assignment_observations(
    boxes: Sequence[PanelBox], regions: Sequence[Any]
) -> tuple[_AssignmentObservation, ...]:
    result: list[_AssignmentObservation] = []
    for region in regions:
        matches = _candidate_group_indices(boxes, region)
        if len(matches) == 1:
            result.append(
                _AssignmentObservation(
                    candidates=matches,
                    status=AssignmentStatus.CONFIDENT,
                    reason="unique-center-containment",
                    assigned_group=matches[0],
                )
            )
        elif not matches:
            result.append(
                _AssignmentObservation(
                    candidates=(),
                    status=AssignmentStatus.UNASSIGNED,
                    reason="no-center-containment",
                    assigned_group=None,
                )
            )
        else:
            result.append(
                _AssignmentObservation(
                    candidates=matches,
                    status=AssignmentStatus.AMBIGUOUS,
                    reason="multiple-center-containment",
                    assigned_group=None,
                )
            )
    return tuple(result)


def _resolve_a1(pixels: Any, regions: Sequence[Any], *, page_height: int) -> _A1Resolution:
    fallback = tuple(manga_tier_order(regions, page_height=page_height))
    if len(regions) < 2:
        return _A1Resolution(
            fallback=fallback,
            segmentation=None,
            observations=(),
            groups=(),
            fallback_ranks=(),
            precedence_edges=(),
            node_order=None,
            uncertain_region_indices=(),
            fallback_reason="fewer-than-two-regions",
            panel_evidence_mode=PanelEvidenceMode.NONE,
            used_panel_evidence=False,
        )

    segmentation = segment_panel_groups(pixels)
    if not segmentation.reliable:
        return _A1Resolution(
            fallback=fallback,
            segmentation=segmentation,
            observations=(),
            groups=tuple(() for _ in segmentation.boxes),
            fallback_ranks=(),
            precedence_edges=(),
            node_order=None,
            uncertain_region_indices=(),
            fallback_reason=segmentation.reason,
            panel_evidence_mode=PanelEvidenceMode.NONE,
            used_panel_evidence=False,
        )

    observations = _build_assignment_observations(segmentation.boxes, regions)
    groups_mutable: list[list[int]] = [[] for _ in segmentation.boxes]
    uncertain: list[int] = []
    for region_index, observation in enumerate(observations):
        if observation.assigned_group is None:
            uncertain.append(region_index)
        else:
            groups_mutable[observation.assigned_group].append(region_index)
    groups = tuple(tuple(group) for group in groups_mutable)
    if sum(bool(group) for group in groups) < 2:
        return _A1Resolution(
            fallback=fallback,
            segmentation=segmentation,
            observations=observations,
            groups=groups,
            fallback_ranks=(),
            precedence_edges=(),
            node_order=None,
            uncertain_region_indices=tuple(uncertain),
            fallback_reason="insufficient-confident-panel-groups",
            panel_evidence_mode=PanelEvidenceMode.NONE,
            used_panel_evidence=False,
        )

    fallback_position = {id(region): index for index, region in enumerate(fallback)}
    panel_ranks = tuple(
        min((fallback_position[id(regions[index])] for index in group), default=None)
        for group in groups
    )
    precedence_edges = _panel_precedence_edges(segmentation.boxes)
    panel_count = len(segmentation.boxes)
    node_count = panel_count + len(uncertain)
    uncertain_by_node = {
        panel_count + offset: region_index for offset, region_index in enumerate(uncertain)
    }

    def tie_key(node_index: int) -> tuple[float, int, int, int, int]:
        if node_index < panel_count:
            box = segmentation.boxes[node_index]
            rank_value = panel_ranks[node_index]
            rank = float(rank_value) if rank_value is not None else float("inf")
            return (rank, 0, box.y1, -box.x2, node_index)
        region_index = uncertain_by_node[node_index]
        rank = float(fallback_position[id(regions[region_index])])
        _, y1, _, _, _, _ = _raw_geometry(regions[region_index])
        x2 = _raw_geometry(regions[region_index])[2]
        return (rank, 1, y1, -x2, region_index)

    node_order = _deterministic_topological_order(
        node_count,
        tuple((edge.source_index, edge.target_index) for edge in precedence_edges),
        tie_key=tie_key,
    )
    if node_order is None:
        return _A1Resolution(
            fallback=fallback,
            segmentation=segmentation,
            observations=observations,
            groups=groups,
            fallback_ranks=panel_ranks,
            precedence_edges=precedence_edges,
            node_order=None,
            uncertain_region_indices=tuple(uncertain),
            fallback_reason="precedence-cycle",
            panel_evidence_mode=PanelEvidenceMode.NONE,
            used_panel_evidence=False,
        )
    mode = PanelEvidenceMode.PARTIAL if uncertain else PanelEvidenceMode.FULL
    return _A1Resolution(
        fallback=fallback,
        segmentation=segmentation,
        observations=observations,
        groups=groups,
        fallback_ranks=panel_ranks,
        precedence_edges=precedence_edges,
        node_order=node_order,
        uncertain_region_indices=tuple(uncertain),
        fallback_reason=None,
        panel_evidence_mode=mode,
        used_panel_evidence=True,
    )


def _b0_local_order(
    regions: Sequence[Any],
    refs_by_object: dict[int, ExperimentRegion],
    *,
    page_height: int,
    tier_prefix: str,
) -> tuple[list[Any], dict[str, tuple[str | None, str | None, str, tuple[object, ...]]]]:
    ordered: list[Any] = []
    metadata: dict[str, tuple[str | None, str | None, str, tuple[object, ...]]] = {}
    for tier_index, tier in enumerate(_partition_manga_tiers(regions, page_height=page_height)):
        tier_id = f"{tier_prefix}t{tier_index:03d}"
        items = sorted(tier, key=lambda item: (-item.x_center, item.y_top, item.source_index))
        for item in items:
            ref = refs_by_object[id(item.region)]
            x1, y1, x2, _, center2x, _ = _raw_geometry(item.region)
            del x1, x2
            key: tuple[object, ...] = (-center2x, 2 * y1, ref.source_index)
            metadata[ref.region_id] = (tier_id, f"{tier_id}-r000", "b0-tier", key)
            ordered.append(item.region)
    return ordered, metadata


def _b1_local_order(
    regions: Sequence[Any],
    refs_by_object: dict[int, ExperimentRegion],
    *,
    page_height: int,
    tier_prefix: str,
) -> tuple[list[Any], dict[str, tuple[str | None, str | None, str, tuple[object, ...]]]]:
    ordered: list[Any] = []
    metadata: dict[str, tuple[str | None, str | None, str, tuple[object, ...]]] = {}
    for tier_index, tier in enumerate(_partition_manga_tiers(regions, page_height=page_height)):
        tier_id = f"{tier_prefix}t{tier_index:03d}"
        horizontal: list[Any] = []
        vertical: list[Any] = []
        ambiguous: list[list[Any]] = []
        for item in tier:
            orientation = _normalize_orientation(getattr(item.region, "direction", ""))
            if orientation is OrientationClass.HORIZONTAL:
                horizontal.append(item)
            elif orientation is OrientationClass.VERTICAL:
                vertical.append(item)
            else:
                ambiguous.append([item])

        subruns: list[tuple[OrientationClass, list[Any]]] = []
        if horizontal:
            subruns.append((OrientationClass.HORIZONTAL, horizontal))
        if vertical:
            subruns.append((OrientationClass.VERTICAL, vertical))
        subruns.extend((OrientationClass.AMBIGUOUS, run) for run in ambiguous)

        def source_index(item: Any) -> int:
            return refs_by_object[id(item.region)].source_index

        prepared: list[tuple[tuple[object, ...], OrientationClass, list[Any]]] = []
        for orientation, run in subruns:
            if orientation is OrientationClass.HORIZONTAL:
                run.sort(key=lambda item: (item.x_center, item.y_top, source_index(item)))
            elif orientation is OrientationClass.VERTICAL:
                run.sort(key=lambda item: (-item.x_center, item.y_top, source_index(item)))
            else:
                run.sort(key=lambda item: source_index(item))
            min_y = min(int(item.y_top) for item in run)
            max_center2x = max(_raw_geometry(item.region)[4] for item in run)
            min_source = min(source_index(item) for item in run)
            schedule_key: tuple[object, ...] = (
                min_y,
                -max_center2x,
                min_source,
                orientation.value,
            )
            prepared.append((schedule_key, orientation, run))
        prepared.sort(key=lambda value: value[0])

        for run_index, (schedule_key, orientation, run) in enumerate(prepared):
            run_id = f"{tier_id}-r{run_index:03d}"
            mode = {
                OrientationClass.HORIZONTAL: "ltr-horizontal",
                OrientationClass.VERTICAL: "rtl-vertical",
                OrientationClass.AMBIGUOUS: "singleton",
            }[orientation]
            mixed_mode = "mixed" if len(prepared) > 1 else mode
            for item in run:
                ref = refs_by_object[id(item.region)]
                _, y1, _, _, center2x, _ = _raw_geometry(item.region)
                intra: tuple[object, ...]
                if orientation is OrientationClass.HORIZONTAL:
                    intra = (center2x, 2 * y1, ref.source_index)
                elif orientation is OrientationClass.VERTICAL:
                    intra = (-center2x, 2 * y1, ref.source_index)
                else:
                    intra = (ref.source_index,)
                metadata[ref.region_id] = (
                    tier_id,
                    run_id,
                    mixed_mode,
                    schedule_key + intra,
                )
                ordered.append(item.region)
    return ordered, metadata


def _fallback_local_metadata(
    fallback: Sequence[Any], refs_by_object: dict[int, ExperimentRegion], *, page_height: int
) -> dict[str, tuple[str | None, str | None, str, tuple[object, ...]]]:
    _, metadata = _b0_local_order(
        fallback,
        refs_by_object,
        page_height=page_height,
        tier_prefix="page-",
    )
    return metadata


def _assignment_from_observations(
    boxes: Sequence[PanelBox], observations: Sequence[_AssignmentObservation]
) -> PanelAssignment:
    groups: list[list[int]] = [[] for _ in boxes]
    for index, observation in enumerate(observations):
        if observation.assigned_group is not None:
            groups[observation.assigned_group].append(index)
    reliable = all(item.assigned_group is not None for item in observations) and sum(
        bool(group) for group in groups
    ) >= 2
    return PanelAssignment(
        groups=tuple(tuple(group) for group in groups),
        reliable=reliable,
        reason="reliable" if reliable else "region-unassigned-or-ambiguous",
    )


def run_reading_order_v2_arm(
    pixels: Any,
    regions: Sequence[ExperimentRegion],
    *,
    page_height: int,
    repository_sha: str,
    page_id: str,
    arm_id: ArmId,
) -> ArmResult:
    """Execute exactly one frozen v2 arm without consulting corpus ground truth."""
    if len({ref.region_id for ref in regions}) != len(regions):
        raise ValueError("experiment region IDs must be unique")
    if len({ref.source_index for ref in regions}) != len(regions):
        raise ValueError("experiment source indexes must be unique")
    raw_regions = tuple(ref.region for ref in regions)
    refs_by_object = {id(ref.region): ref for ref in regions}
    if len(refs_by_object) != len(regions):
        raise ValueError("one runtime region object cannot back multiple experiment regions")

    if arm_id.partial_panel_evidence:
        resolution: Any = _resolve_a1(pixels, raw_regions, page_height=page_height)
        segmentation = resolution.segmentation
        observations = resolution.observations
        groups = resolution.groups
        fallback = resolution.fallback
        fallback_ranks = resolution.fallback_ranks
        edges = resolution.precedence_edges
        panel_evidence_mode = resolution.panel_evidence_mode
        fallback_reason = resolution.fallback_reason
        used_panel_evidence = resolution.used_panel_evidence
        node_order = resolution.node_order
        assignment_policy = "partial-panel-evidence-v1"
    else:
        production = _resolve_panel_flow_v1(pixels, raw_regions, page_height=page_height)
        segmentation = production.segmentation
        observations = (
            _build_assignment_observations(segmentation.boxes, raw_regions)
            if segmentation is not None and segmentation.reliable
            else ()
        )
        groups = production.assignment.groups if production.assignment is not None else ()
        fallback = production.fallback
        fallback_ranks = production.fallback_ranks
        edges = production.precedence_edges
        panel_evidence_mode = (
            PanelEvidenceMode.FULL if production.used_panel_evidence else PanelEvidenceMode.NONE
        )
        fallback_reason = production.fallback_reason
        used_panel_evidence = production.used_panel_evidence
        node_order = production.panel_order
        assignment_policy = "all-or-nothing-v1"

    fallback_order = tuple(refs_by_object[id(region)].region_id for region in fallback)
    fallback_position = {region_id: index for index, region_id in enumerate(fallback_order)}
    local_metadata: dict[str, tuple[str | None, str | None, str, tuple[object, ...]]] = {}

    if not used_panel_evidence:
        ordered_raw = tuple(fallback)
        local_metadata = _fallback_local_metadata(
            fallback, refs_by_object, page_height=page_height
        )
    elif not arm_id.partial_panel_evidence:
        assert segmentation is not None
        assert node_order is not None
        if not arm_id.orientation_aware_local_order:
            ordered_raw = _materialize_panel_flow_v1(
                production, raw_regions, page_height=page_height
            )
            for group_index in node_order:
                group_regions = [raw_regions[index] for index in groups[group_index]]
                _, metadata = _b0_local_order(
                    group_regions,
                    refs_by_object,
                    page_height=page_height,
                    tier_prefix=f"g{group_index:03d}-",
                )
                local_metadata.update(metadata)
        else:
            materialized: list[Any] = []
            for group_index in node_order:
                group_regions = [raw_regions[index] for index in groups[group_index]]
                local, metadata = _b1_local_order(
                    group_regions,
                    refs_by_object,
                    page_height=page_height,
                    tier_prefix=f"g{group_index:03d}-",
                )
                materialized.extend(local)
                local_metadata.update(metadata)
            ordered_raw = tuple(materialized)
    else:
        assert segmentation is not None
        assert node_order is not None
        panel_count = len(segmentation.boxes)
        uncertain_by_node = {
            panel_count + offset: region_index
            for offset, region_index in enumerate(resolution.uncertain_region_indices)
        }
        materialized = []
        for node_index in node_order:
            if node_index < panel_count:
                group_regions = [raw_regions[index] for index in groups[node_index]]
                if arm_id.orientation_aware_local_order:
                    local, metadata = _b1_local_order(
                        group_regions,
                        refs_by_object,
                        page_height=page_height,
                        tier_prefix=f"g{node_index:03d}-",
                    )
                else:
                    local, metadata = _b0_local_order(
                        group_regions,
                        refs_by_object,
                        page_height=page_height,
                        tier_prefix=f"g{node_index:03d}-",
                    )
                materialized.extend(local)
                local_metadata.update(metadata)
            else:
                region_index = uncertain_by_node[node_index]
                raw = raw_regions[region_index]
                ref = refs_by_object[id(raw)]
                materialized.append(raw)
                local_metadata[ref.region_id] = (
                    "uncertain",
                    f"uncertain-r{region_index:03d}",
                    "singleton",
                    (fallback_position[ref.region_id], ref.source_index),
                )
        ordered_raw = tuple(materialized)

    if len(ordered_raw) != len(raw_regions) or {id(item) for item in ordered_raw} != {
        id(item) for item in raw_regions
    }:
        raise AssertionError("Reading Order v2 arm changed the input region set")
    ordered_refs = tuple(refs_by_object[id(region)] for region in ordered_raw)
    final_position = {ref.region_id: index for index, ref in enumerate(ordered_refs)}

    if segmentation is None:
        segmentation_diag = SegmentationDiagnostic(False, False, "not-attempted", 0)
        observations_for_diag = tuple(
            _AssignmentObservation((), AssignmentStatus.UNASSIGNED, "not-attempted", None)
            for _ in raw_regions
        )
        boxes: tuple[PanelBox, ...] = ()
    else:
        segmentation_diag = SegmentationDiagnostic(
            True, segmentation.reliable, segmentation.reason, len(segmentation.boxes)
        )
        boxes = segmentation.boxes
        observations_for_diag = observations or tuple(
            _AssignmentObservation((), AssignmentStatus.UNASSIGNED, "not-attempted", None)
            for _ in raw_regions
        )

    group_diags: list[GroupDiagnostic] = []
    panel_order_positions = {
        index: position
        for position, index in enumerate(node_order or ())
        if index < len(boxes)
    }
    for group_index, box in enumerate(boxes):
        group_id = f"g{group_index:03d}"
        candidate_ids = tuple(
            ref.region_id
            for ref, observation in zip(regions, observations_for_diag, strict=True)
            if group_index in observation.candidates
        )
        confident_ids = tuple(
            ref.region_id
            for ref, observation in zip(regions, observations_for_diag, strict=True)
            if observation.assigned_group == group_index
        )
        rank = fallback_ranks[group_index] if group_index < len(fallback_ranks) else None
        edge_diags = tuple(
            PrecedenceEdgeDiagnostic(
                target_group_id=f"g{edge.target_index:03d}",
                rule=edge.rule,
                x_overlap=RationalDiagnostic(
                    edge.x_overlap.numerator, edge.x_overlap.denominator
                ),
                y_overlap=RationalDiagnostic(
                    edge.y_overlap.numerator, edge.y_overlap.denominator
                ),
            )
            for edge in edges
            if edge.source_index == group_index
        )
        group_diags.append(
            GroupDiagnostic(
                group_id=group_id,
                source_group_index=group_index,
                bbox=(box.x1, box.y1, box.x2, box.y2),
                center2x=box.x1 + box.x2,
                center2y=box.y1 + box.y2,
                candidate_region_ids=candidate_ids,
                confident_region_ids=confident_ids,
                fallback_rank=rank,
                precedence_index=panel_order_positions.get(group_index),
                tie_key=(rank, box.y1, -box.x2, group_index),
                precedence_edges=edge_diags,
            )
        )

    region_diags: list[RegionDiagnostic] = []
    for ref, observation in zip(regions, observations_for_diag, strict=True):
        raw = ref.region
        x1, y1, x2, y2, center2x, center2y = _raw_geometry(raw)
        polygon = tuple((int(point[0]), int(point[1])) for point in raw.min_rect[0])
        direction = str(getattr(raw, "direction", ""))
        tier_id, run_id, mode, local_key = local_metadata.get(
            ref.region_id, (None, None, "page-fallback", ())
        )
        region_diags.append(
            RegionDiagnostic(
                region_id=ref.region_id,
                source_index=ref.source_index,
                bbox=(x1, y1, x2, y2),
                polygon=polygon,
                center2x=center2x,
                center2y=center2y,
                direction_raw=direction,
                orientation_class=_normalize_orientation(direction),
                candidate_group_ids=tuple(f"g{index:03d}" for index in observation.candidates),
                assignment_status=observation.status,
                assignment_reason=observation.reason,
                ambiguity_count=len(observation.candidates),
                assigned_group_id=(
                    f"g{observation.assigned_group:03d}"
                    if observation.assigned_group is not None
                    else None
                ),
                fallback_index=fallback_position[ref.region_id],
                local_tier_id=tier_id,
                local_run_id=run_id,
                local_ordering_mode=mode,
                local_ordering_key=local_key,
                final_page_index=final_position[ref.region_id],
            )
        )

    diagnostic = PageDiagnostic(
        schema_version=SCHEMA_VERSION,
        spec_version=SPEC_VERSION,
        repository_sha=repository_sha,
        arm_id=arm_id,
        page_id=page_id,
        input_region_count=len(regions),
        input_region_ids=tuple(ref.region_id for ref in regions),
        fallback_order=fallback_order,
        segmentation=segmentation_diag,
        assignment_policy=assignment_policy,
        panel_evidence_mode=panel_evidence_mode,
        used_panel_evidence=used_panel_evidence,
        fallback_reason=fallback_reason,
        final_order=tuple(ref.region_id for ref in ordered_refs),
        groups=tuple(group_diags),
        regions=tuple(region_diags),
    )
    return ArmResult(ordered_regions=ordered_refs, diagnostic=diagnostic)
