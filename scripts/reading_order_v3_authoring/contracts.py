from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

DESIGN_SCHEMA_VERSION = "reading-order-v3-authoring-design-v1"
INPUT_SCHEMA_VERSION = "reading-order-v3-authoring-input-v1"
ANNOTATION_SCHEMA_VERSION = "reading-order-v3-authoring-annotation-v1"
MANIFEST_SCHEMA_VERSION = "reading-order-v3-authoring-manifest-v1"
AUTHORSHIP_BOUNDARY = "isolated-candidate-independent-v3"

POSITIVE_FAMILIES = (
    "c1-boundary-positive",
    "c2-gutter-bridge",
    "c2-ambiguous-overlap-bridge",
    "c2-pair-precedence-slot",
    "c3-positive-recovery",
    "b1-horizontal",
    "b1-vertical",
    "b1-mixed-orientation",
)
C3_REJECTION_FAMILY = "c3_rejection_pages"

DESIGN_MINIMA = {
    "minimumPageCount": 24,
    "minimumQualificationPairs": 120,
    "minimumScoredRegions": 96,
    "minimumCombinedMechanismPages": 4,
    "minimumIntentionalFallbackPages": 3,
    "minimumCleanControlPages": 6,
}

SLICE_MINIMA: dict[str, dict[str, int]] = {
    "c1-boundary-positive": {"minPairs": 10, "minPages": 4},
    "c1-near-boundary-negative": {"minPairs": 8, "minPages": 4},
    "c2-gutter-bridge": {"minPairs": 8, "minPages": 3},
    "c2-ambiguous-overlap-bridge": {"minPairs": 8, "minPages": 3},
    "c2-pair-precedence-slot": {"minPairs": 8, "minPages": 3},
    "c2-one-sided-non-unique-fail-closed": {"minPairs": 8, "minPages": 3},
    "c2-conflict-cycle-safety": {"minPairs": 6, "minPages": 2},
    "c3-positive-recovery": {"minPairs": 10, "minPages": 4},
    "b1-horizontal": {"minPairs": 10, "minPages": 4},
    "b1-vertical": {"minPairs": 10, "minPages": 4},
    "b1-mixed-orientation": {"minPairs": 10, "minPages": 4},
    "combined-c1-c2-c3-b1": {"minPairs": 12, "minPages": 4},
    "clean-control": {"minPairs": 16, "minPages": 6},
    "intentional-fallback": {"minPairs": 8, "minPages": 3},
}
AUTHORING_SLICES = tuple(SLICE_MINIMA)

_COMBINED_SLICE = "combined-c1-c2-c3-b1"
_CLEAN_CONTROL_SLICE = "clean-control"
_INTENTIONAL_FALLBACK_SLICE = "intentional-fallback"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_PAGE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RegionInput:
    region_id: str
    source_index: int
    lines: tuple[tuple[tuple[int, int], ...], ...]
    angle: float


@dataclass(frozen=True, slots=True)
class PageInput:
    page_id: str
    width: int
    height: int
    regions: tuple[RegionInput, ...]


@dataclass(frozen=True, slots=True)
class QualificationPair:
    pair_id: str
    earlier: str
    later: str
    slices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageAnnotation:
    page_id: str
    reading_order: tuple[str, ...]
    unscored_region_ids: tuple[str, ...]
    qualification_pairs: tuple[QualificationPair, ...]


@dataclass(frozen=True, slots=True)
class PageAuthoringRecord:
    page_id: str
    source: str
    image: str
    input: str
    annotation: str
    positive_families: tuple[str, ...]
    primary_positive_family: str | None
    c3_rejection: bool


@dataclass(frozen=True, slots=True)
class CorpusDesign:
    corpus_id: str
    version: str
    pages: tuple[PageAuthoringRecord, ...]


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    dedicated_positive_pages: dict[str, tuple[str, ...]]
    c3_rejection_pages: tuple[str, ...]
    slice_pair_counts: dict[str, int]
    slice_page_counts: dict[str, int]
    design_role_pages: dict[str, tuple[str, ...]]
    total_qualification_pairs: int
    total_scored_regions: int


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{path}: invalid JSON object") from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{path}: expected JSON object")
    return value


def _exact_keys(data: dict[str, Any], required: set[str], where: str) -> None:
    if set(data) != required:
        raise ContractError(
            f"{where}: property mismatch: missing={sorted(required-set(data))}, "
            f"extra={sorted(set(data)-required)}"
        )


def _identifier(value: object, where: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ContractError(f"{where}: invalid identifier")
    return value


def _page_id(value: object, where: str) -> str:
    if not isinstance(value, str) or _PAGE_ID_RE.fullmatch(value) is None:
        raise ContractError(f"{where}: invalid page ID")
    return value


def _region_id(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{where}: nonempty string required")
    return value


def _relative_path(value: object, where: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ContractError(f"{where}: safe normalized POSIX relative path required")
    pure = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        pure.is_absolute()
        or bool(windows.drive)
        or ".." in pure.parts
        or "." in pure.parts
        or pure.as_posix() != value
    ):
        raise ContractError(f"{where}: safe normalized POSIX relative path required")
    if value in {"manifest.json", "corpus-design.json"}:
        raise ContractError(f"{where}: reserved authoring path")
    return value


def _string_array(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractError(f"{where}: string array required")
    return tuple(value)


def _runtime_angle(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError(f"{where}: finite binary64 number required")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContractError(f"{where}: finite binary64 number required")
        return value
    try:
        runtime_value = float(value)
    except OverflowError as exc:
        raise ContractError(f"{where}: finite binary64 number required") from exc
    if not math.isfinite(runtime_value) or int(runtime_value) != value:
        raise ContractError(f"{where}: integer must be exactly representable as finite binary64")
    return runtime_value


def load_design(path: Path) -> CorpusDesign:
    data = _load_object(path)
    _exact_keys(
        data,
        {
            "schemaVersion",
            "corpusId",
            "version",
            "authorshipBoundary",
            "provenanceDeclaration",
            "pages",
        },
        "corpus-design",
    )
    if data["schemaVersion"] != DESIGN_SCHEMA_VERSION:
        raise ContractError("corpus-design: bad schema version")
    corpus_id = _identifier(data["corpusId"], "corpus-design.corpusId")
    version = _identifier(data["version"], "corpus-design.version")
    if data["authorshipBoundary"] != AUTHORSHIP_BOUNDARY:
        raise ContractError("corpus-design: wrong authorship boundary")

    provenance = data["provenanceDeclaration"]
    expected_provenance = {
        "priorHeldoutEvidenceInspected": False,
        "calibrationOutputsInspected": False,
        "candidateDiagnosticsInspected": False,
        "candidateExecuted": False,
        "qualificationExecuted": False,
        "annotationsAdaptedToCandidateOutput": False,
    }
    if provenance != expected_provenance:
        raise ContractError("corpus-design: clean-room provenance declaration is not satisfied")

    raw_pages = data["pages"]
    if not isinstance(raw_pages, list):
        raise ContractError("corpus-design.pages: array required")
    if len(raw_pages) < DESIGN_MINIMA["minimumPageCount"]:
        raise ContractError(
            f"corpus-design.pages: at least {DESIGN_MINIMA['minimumPageCount']} pages required"
        )

    pages: list[PageAuthoringRecord] = []
    seen_page_ids: set[str] = set()
    seen_paths: set[str] = set()
    positive_set = set(POSITIVE_FAMILIES)
    for index, raw in enumerate(raw_pages):
        where = f"corpus-design.pages[{index}]"
        if not isinstance(raw, dict):
            raise ContractError(f"{where}: object required")
        _exact_keys(
            raw,
            {"pageId", "source", "image", "input", "annotation", "authoringCoverage"},
            where,
        )
        page_id = _page_id(raw["pageId"], f"{where}.pageId")
        if page_id in seen_page_ids:
            raise ContractError(f"corpus-design: duplicate page ID {page_id}")
        seen_page_ids.add(page_id)

        role_paths = {
            role: _relative_path(raw[role], f"{where}.{role}")
            for role in ("source", "image", "input", "annotation")
        }
        for role, relative in role_paths.items():
            if relative in seen_paths:
                raise ContractError(f"corpus-design: duplicate role path {relative} ({role})")
            seen_paths.add(relative)

        coverage = raw["authoringCoverage"]
        if not isinstance(coverage, dict):
            raise ContractError(f"{where}.authoringCoverage: object required")
        _exact_keys(
            coverage,
            {"positiveFamilies", "primaryPositiveFamily", "c3Rejection"},
            f"{where}.authoringCoverage",
        )
        positive_families = _string_array(
            coverage["positiveFamilies"], f"{where}.authoringCoverage.positiveFamilies"
        )
        if len(set(positive_families)) != len(positive_families):
            raise ContractError(f"{where}: duplicate positive family")
        unknown = sorted(set(positive_families) - positive_set)
        if unknown:
            raise ContractError(f"{where}: unknown positive authoring families {unknown}")
        primary = coverage["primaryPositiveFamily"]
        if primary is not None and (not isinstance(primary, str) or primary not in positive_set):
            raise ContractError(f"{where}.authoringCoverage.primaryPositiveFamily: invalid")
        if primary is not None and primary not in positive_families:
            raise ContractError(f"{where}: primary positive family must be declared on the page")
        c3_rejection = coverage["c3Rejection"]
        if not isinstance(c3_rejection, bool):
            raise ContractError(f"{where}.authoringCoverage.c3Rejection: boolean required")
        pages.append(
            PageAuthoringRecord(
                page_id=page_id,
                source=role_paths["source"],
                image=role_paths["image"],
                input=role_paths["input"],
                annotation=role_paths["annotation"],
                positive_families=positive_families,
                primary_positive_family=primary,
                c3_rejection=c3_rejection,
            )
        )
    return CorpusDesign(corpus_id=corpus_id, version=version, pages=tuple(pages))


def load_input(path: Path) -> PageInput:
    data = _load_object(path)
    _exact_keys(data, {"schemaVersion", "pageId", "width", "height", "regions"}, str(path))
    if data["schemaVersion"] != INPUT_SCHEMA_VERSION:
        raise ContractError(f"{path}: bad input schema version")
    page_id = _page_id(data["pageId"], f"{path}.pageId")
    width, height = data["width"], data["height"]
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise ContractError(f"{path}: positive integer dimensions required")
    raw_regions = data["regions"]
    if not isinstance(raw_regions, list) or len(raw_regions) < 2:
        raise ContractError(f"{path}.regions: at least two regions required")
    regions: list[RegionInput] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    for position, raw in enumerate(raw_regions):
        where = f"{path}.regions[{position}]"
        if not isinstance(raw, dict):
            raise ContractError(f"{where}: object required")
        _exact_keys(raw, {"regionId", "sourceIndex", "lines", "angle"}, where)
        region_id = _region_id(raw["regionId"], f"{where}.regionId")
        source_index = raw["sourceIndex"]
        if isinstance(source_index, bool) or not isinstance(source_index, int) or source_index < 0:
            raise ContractError(f"{where}.sourceIndex: nonnegative integer required")
        if region_id in seen_ids or source_index in seen_indexes:
            raise ContractError(f"{path}: duplicate region identity/index")
        seen_ids.add(region_id)
        seen_indexes.add(source_index)
        raw_lines = raw["lines"]
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ContractError(f"{where}.lines: nonempty array required")
        lines: list[tuple[tuple[int, int], ...]] = []
        for line_index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, list) or len(raw_line) != 4:
                raise ContractError(f"{where}.lines[{line_index}]: quadrilateral required")
            points: list[tuple[int, int]] = []
            for raw_point in raw_line:
                if (
                    not isinstance(raw_point, list)
                    or len(raw_point) != 2
                    or any(
                        isinstance(item, bool) or not isinstance(item, int) for item in raw_point
                    )
                ):
                    raise ContractError(f"{where}: line points must be integer [x,y] pairs")
                x, y = raw_point
                if not (0 <= x <= width and 0 <= y <= height):
                    raise ContractError(f"{where}: line point outside page")
                points.append((x, y))
            lines.append(tuple(points))
        angle = _runtime_angle(raw["angle"], f"{where}.angle")
        regions.append(RegionInput(region_id, source_index, tuple(lines), angle))
    if sorted(seen_indexes) != list(range(len(seen_indexes))):
        raise ContractError(f"{path}: sourceIndex must be contiguous from zero")
    return PageInput(
        page_id=page_id,
        width=width,
        height=height,
        regions=tuple(sorted(regions, key=lambda item: item.source_index)),
    )


def load_annotation(path: Path) -> PageAnnotation:
    data = _load_object(path)
    _exact_keys(
        data,
        {"schemaVersion", "pageId", "readingOrder", "unscoredRegionIds", "qualificationPairs"},
        str(path),
    )
    if data["schemaVersion"] != ANNOTATION_SCHEMA_VERSION:
        raise ContractError(f"{path}: bad annotation schema version")
    page_id = _page_id(data["pageId"], f"{path}.pageId")
    reading = _string_array(data["readingOrder"], f"{path}.readingOrder")
    unscored = _string_array(data["unscoredRegionIds"], f"{path}.unscoredRegionIds")
    if not reading:
        raise ContractError(f"{path}.readingOrder: nonempty string array required")
    for region_id in (*reading, *unscored):
        _region_id(region_id, f"{path}: region ID")
    if len(set(reading)) != len(reading):
        raise ContractError(f"{path}.readingOrder: duplicate region ID")
    if len(set(unscored)) != len(unscored):
        raise ContractError(f"{path}.unscoredRegionIds: duplicate region ID")
    if set(reading) & set(unscored):
        raise ContractError(f"{path}: scored and unscored region IDs overlap")

    raw_pairs = data["qualificationPairs"]
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ContractError(f"{path}.qualificationPairs: nonempty array required")
    position = {region_id: index for index, region_id in enumerate(reading)}
    pairs: list[QualificationPair] = []
    seen_pair_ids: set[str] = set()
    seen_endpoints: set[tuple[str, str]] = set()
    allowed_slices = set(AUTHORING_SLICES)
    for index, raw in enumerate(raw_pairs):
        where = f"{path}.qualificationPairs[{index}]"
        if not isinstance(raw, dict):
            raise ContractError(f"{where}: object required")
        _exact_keys(raw, {"id", "earlier", "later", "slices"}, where)
        pair_id = raw["id"]
        earlier = raw["earlier"]
        later = raw["later"]
        if not isinstance(pair_id, str) or not pair_id:
            raise ContractError(f"{where}.id: nonempty string required")
        earlier_id = _region_id(earlier, f"{where}.earlier")
        later_id = _region_id(later, f"{where}.later")
        if pair_id in seen_pair_ids or (earlier_id, later_id) in seen_endpoints:
            raise ContractError(f"{path}: duplicate qualification pair")
        if earlier_id not in position or later_id not in position:
            raise ContractError(f"{where}: endpoints must both be scored regions")
        if position[earlier_id] >= position[later_id]:
            raise ContractError(f"{where}: must follow ground-truth precedence")
        slices = _string_array(raw["slices"], f"{where}.slices")
        if not slices:
            raise ContractError(f"{where}.slices: nonempty array required")
        if len(set(slices)) != len(slices):
            raise ContractError(f"{where}.slices: duplicate slice")
        if tuple(sorted(slices)) != slices:
            raise ContractError(f"{where}.slices: canonical sorted order required")
        unknown = sorted(set(slices) - allowed_slices)
        if unknown:
            raise ContractError(f"{where}: unknown or forbidden authoring slices {unknown}")
        seen_pair_ids.add(pair_id)
        seen_endpoints.add((earlier_id, later_id))
        pairs.append(QualificationPair(pair_id, earlier_id, later_id, slices))
    return PageAnnotation(page_id, reading, unscored, tuple(pairs))


def validate_authoring_coverage(
    design: CorpusDesign,
    annotations: Mapping[str, PageAnnotation],
) -> CoverageSummary:
    design_ids = {page.page_id for page in design.pages}
    if set(annotations) != design_ids:
        raise ContractError("authoring coverage: annotation page inventory must equal design")

    design_by_id = {page.page_id: page for page in design.pages}
    positive_set = set(POSITIVE_FAMILIES)
    authored_positive_by_page: dict[str, frozenset[str]] = {}
    pair_counts = {name: 0 for name in AUTHORING_SLICES}
    page_sets = {name: set() for name in AUTHORING_SLICES}
    total_pairs = 0
    total_scored_regions = 0
    for page_id, annotation in annotations.items():
        if annotation.page_id != page_id:
            raise ContractError(f"{page_id}: annotation identity mismatch")
        authored_positive_slices = frozenset(
            slice_name
            for pair in annotation.qualification_pairs
            for slice_name in pair.slices
            if slice_name in positive_set
        )
        declared_positive_families = set(design_by_id[page_id].positive_families)
        if authored_positive_slices != declared_positive_families:
            raise ContractError(
                f"{page_id}: positiveFamilies must exactly match qualification-pair positive "
                f"slices: declared={sorted(declared_positive_families)}, "
                f"authored={sorted(authored_positive_slices)}"
            )
        authored_positive_by_page[page_id] = authored_positive_slices
        total_pairs += len(annotation.qualification_pairs)
        total_scored_regions += len(annotation.reading_order)
        for pair in annotation.qualification_pairs:
            for slice_name in pair.slices:
                pair_counts[slice_name] += 1
                page_sets[slice_name].add(page_id)

    if total_pairs < DESIGN_MINIMA["minimumQualificationPairs"]:
        raise ContractError(
            "authoring coverage: qualification pairs below frozen minimum: "
            f"{total_pairs} < {DESIGN_MINIMA['minimumQualificationPairs']}"
        )
    if total_scored_regions < DESIGN_MINIMA["minimumScoredRegions"]:
        raise ContractError(
            "authoring coverage: scored regions below frozen minimum: "
            f"{total_scored_regions} < {DESIGN_MINIMA['minimumScoredRegions']}"
        )

    design_role_pages = {
        "combined-mechanism": tuple(sorted(page_sets[_COMBINED_SLICE])),
        "intentional-fallback": tuple(sorted(page_sets[_INTENTIONAL_FALLBACK_SLICE])),
        "clean-control": tuple(sorted(page_sets[_CLEAN_CONTROL_SLICE])),
    }
    role_requirements = {
        "combined-mechanism": "minimumCombinedMechanismPages",
        "intentional-fallback": "minimumIntentionalFallbackPages",
        "clean-control": "minimumCleanControlPages",
    }
    for role, requirement_name in role_requirements.items():
        if len(design_role_pages[role]) < DESIGN_MINIMA[requirement_name]:
            raise ContractError(
                f"authoring coverage: {role} pages below frozen design minimum"
            )

    for name, minima in SLICE_MINIMA.items():
        if pair_counts[name] < minima["minPairs"] or len(page_sets[name]) < minima["minPages"]:
            raise ContractError(
                f"authoring coverage: slice {name} below frozen minima: "
                f"pairs={pair_counts[name]}, pages={len(page_sets[name])}"
            )

    combined_pages = set(design_role_pages["combined-mechanism"])
    dedicated = {family: [] for family in POSITIVE_FAMILIES}
    c3_pages: list[str] = []
    for page in design.pages:
        authored_positive_slices = authored_positive_by_page[page.page_id]
        if page.page_id not in combined_pages and len(authored_positive_slices) == 1:
            family = next(iter(authored_positive_slices))
            if (
                page.positive_families == (family,)
                and page.primary_positive_family == family
            ):
                dedicated[family].append(page.page_id)
        if page.c3_rejection:
            c3_pages.append(page.page_id)
    missing = [family for family in POSITIVE_FAMILIES if not dedicated[family]]
    if missing:
        raise ContractError(f"authoring coverage: missing dedicated positive families {missing}")
    if len(c3_pages) < 8:
        raise ContractError(
            f"authoring coverage: {C3_REJECTION_FAMILY} requires at least 8 unique pages"
        )

    return CoverageSummary(
        dedicated_positive_pages={
            family: tuple(page_ids) for family, page_ids in dedicated.items()
        },
        c3_rejection_pages=tuple(c3_pages),
        slice_pair_counts=dict(pair_counts),
        slice_page_counts={name: len(page_sets[name]) for name in AUTHORING_SLICES},
        design_role_pages=design_role_pages,
        total_qualification_pairs=total_pairs,
        total_scored_regions=total_scored_regions,
    )
