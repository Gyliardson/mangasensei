from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from scripts.reading_order_v3_authoring.contracts import (
    PageAnnotation as CleanRoomPageAnnotation,
)
from scripts.reading_order_v3_authoring.contracts import load_annotation as load_clean_annotation
from scripts.reading_order_v3_authoring.contracts import load_design as load_clean_design
from scripts.reading_order_v3_authoring.contracts import load_input as load_clean_input

from .contracts import ArmId, ArmPageInput, PageGroundTruth, QualificationPair, RegionFixture
from .exercise import ExerciseCount, ExerciseReport, build_exercise_report
from .exercise_v3 import (
    EXERCISE_MINIMA_V3,
    V3DiagnosticValidationError,
    V3TrustedPageInput,
    _c3_generic_rejects,
    _cross_arm_equal,
    _diagnostic,
    _required_arms_by_page,
)

C3_REJECTION_ARMS = frozenset(
    {ArmId.C3_ONLY, ArmId.C1_C2_C3, ArmId.C1_C2_C3_B1}
)


def load_arm_input(path: Path) -> ArmPageInput:
    """Map clean-room serialization to canonical ArmPageInput without reinterpretation."""

    page = load_clean_input(path)
    return ArmPageInput(
        page_id=page.page_id,
        width=page.width,
        height=page.height,
        regions=tuple(
            RegionFixture(
                region_id=region.region_id,
                source_index=region.source_index,
                lines=region.lines,
                angle=region.angle,
            )
            for region in page.regions
        ),
    )


def _to_page_ground_truth(annotation: CleanRoomPageAnnotation) -> PageGroundTruth:
    return PageGroundTruth(
        page_id=annotation.page_id,
        reading_order=annotation.reading_order,
        unscored_region_ids=annotation.unscored_region_ids,
        qualification_pairs=tuple(
            QualificationPair(
                pair_id=pair.pair_id,
                earlier=pair.earlier,
                later=pair.later,
                slices=pair.slices,
            )
            for pair in annotation.qualification_pairs
        ),
        layout_tags=(),
    )


def load_clean_room_annotations(
    corpus_root: Path,
) -> tuple[tuple[PageGroundTruth, ...], frozenset[str]]:
    """Load sealed human-authored GT plus generic C3 page witnesses deterministically.

    The caller must already have completed the inherited sealed-corpus preflight.
    No layout tag or causal C3 category is inferred or authored here.
    """

    design = load_clean_design(corpus_root / "corpus-design.json")
    annotations: list[PageGroundTruth] = []
    c3_rejection_page_ids: set[str] = set()
    for page in design.pages:
        clean = load_clean_annotation(corpus_root / page.annotation)
        if clean.page_id != page.page_id:
            raise ValueError(f"{page.page_id}: clean-room annotation pageId mismatch")
        annotations.append(_to_page_ground_truth(clean))
        if page.c3_rejection:
            c3_rejection_page_ids.add(page.page_id)
    return tuple(annotations), frozenset(c3_rejection_page_ids)


def required_arms_by_page(
    annotations: tuple[PageGroundTruth, ...],
    *,
    c3_rejection_page_ids: frozenset[str],
) -> dict[str, frozenset[ArmId]]:
    """Extend frozen v3 arm selection with generic page-level C3 witnesses."""

    page_ids = {page.page_id for page in annotations}
    unknown = c3_rejection_page_ids - page_ids
    if unknown:
        raise ValueError(
            "generic C3 rejection page IDs missing from annotations: "
            f"{sorted(unknown)}"
        )
    required = {
        page_id: set(arms) for page_id, arms in _required_arms_by_page(annotations).items()
    }
    for page_id in c3_rejection_page_ids:
        required.setdefault(page_id, set()).update(C3_REJECTION_ARMS)
    return {
        page_id: frozenset(arms)
        for page_id, arms in required.items()
        if arms
    }


def validate_diagnostics_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: object,
    trusted_page_inputs: Mapping[str, V3TrustedPageInput],
    c3_rejection_page_ids: frozenset[str],
) -> None:
    """Apply frozen v3 diagnostic authentication to clean-room-required arms."""

    if not isinstance(diagnostics, dict):
        raise V3DiagnosticValidationError(
            ("diagnostics: top-level arm mapping object required",)
        )
    if not isinstance(trusted_page_inputs, Mapping):
        raise V3DiagnosticValidationError(
            ("trusted_page_inputs: page mapping required",)
        )

    problems: list[str] = []
    required = required_arms_by_page(
        annotations,
        c3_rejection_page_ids=c3_rejection_page_ids,
    )
    page_by_id = {page.page_id: page for page in annotations}
    execution_shas: set[str] = set()

    for page_id, arms in required.items():
        page = page_by_id[page_id]
        trusted = trusted_page_inputs.get(page_id)
        if not isinstance(trusted, V3TrustedPageInput):
            problems.append(
                f"trusted_page_inputs[{page_id}]: V3TrustedPageInput required"
            )
            continue

        pre_states: list[tuple[ArmId, object]] = []
        fallback_orders: list[tuple[ArmId, object]] = []
        directions: list[tuple[ArmId, object]] = []
        c3_states: list[tuple[ArmId, object]] = []
        for arm in sorted(arms, key=lambda value: value.value):
            arm_pages = diagnostics.get(arm)
            if not isinstance(arm_pages, dict):
                problems.append(
                    f"diagnostics[{arm.value}]: required arm mapping missing/malformed"
                )
                continue
            if page_id not in arm_pages:
                problems.append(
                    f"diagnostics[{arm.value}][{page_id}]: required page missing"
                )
                continue
            diagnostic = arm_pages[page_id]
            execution_sha = _diagnostic(
                diagnostic,
                arm=arm,
                page=page,
                trusted=trusted,
                problems=problems,
            )
            if execution_sha is not None:
                execution_shas.add(execution_sha)
            if isinstance(diagnostic, dict):
                pre_states.append((arm, diagnostic.get("preSegmentation")))
                fallback_orders.append((arm, diagnostic.get("fallbackOrder")))
                directions.append((arm, diagnostic.get("regionDirections")))
                if arm.c3:
                    c3_states.append(
                        (
                            arm,
                            (
                                diagnostic.get("segmentation"),
                                diagnostic.get("recoveryReason"),
                            ),
                        )
                    )

        _cross_arm_equal(pre_states, page_id, "preSegmentation", problems)
        _cross_arm_equal(fallback_orders, page_id, "fallbackOrder", problems)
        _cross_arm_equal(directions, page_id, "regionDirections", problems)
        _cross_arm_equal(c3_states, page_id, "C3 segmentation/recoveryReason", problems)

    if len(execution_shas) > 1:
        problems.append(
            "diagnostics: required pages/arms use inconsistent executionSha values"
        )
    if problems:
        raise V3DiagnosticValidationError(problems)


def generic_c3_rejection_pages(
    *,
    c3_rejection_page_ids: frozenset[str],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
) -> set[str]:
    """Count each declared generic C3 rejection page at most once."""

    pages: set[str] = set()
    for page_id in c3_rejection_page_ids:
        if any(
            _c3_generic_rejects(diagnostics[arm][page_id])
            for arm in C3_REJECTION_ARMS
        ):
            pages.add(page_id)
    return pages


def build_exercise_report_v3(
    *,
    annotations: tuple[PageGroundTruth, ...],
    diagnostics: dict[ArmId, dict[str, dict[str, object]]],
    trusted_page_inputs: Mapping[str, V3TrustedPageInput],
    c3_rejection_page_ids: frozenset[str],
) -> ExerciseReport:
    """Build the frozen v3 report with generic clean-room C3 page witnesses."""

    validate_diagnostics_v3(
        annotations=annotations,
        diagnostics=diagnostics,
        trusted_page_inputs=trusted_page_inputs,
        c3_rejection_page_ids=c3_rejection_page_ids,
    )
    v2 = build_exercise_report(annotations=annotations, diagnostics=diagnostics)
    counts = {
        name: v2.counts[name]
        for name in EXERCISE_MINIMA_V3
        if name != "c3_rejection_pages"
    }
    c3_pages = generic_c3_rejection_pages(
        c3_rejection_page_ids=c3_rejection_page_ids,
        diagnostics=diagnostics,
    )
    ordered = tuple(sorted(c3_pages))
    counts["c3_rejection_pages"] = ExerciseCount(len(ordered), (), ordered)
    return ExerciseReport(counts=counts, minima=dict(EXERCISE_MINIMA_V3))
