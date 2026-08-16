from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import (
    ANNOTATION_SCHEMA_VERSION,
    CORPUS_DESIGN_SCHEMA_VERSION,
    CORPUS_MANIFEST_SCHEMA_VERSION,
    INPUT_SCHEMA_VERSION,
)

HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
PAGE_ID_RE = re.compile(r"^Q[0-9]{3}$")
QUALIFICATION_ID_RE = re.compile(r"^ropv2q-[0-9a-f]{64}$")

REQUIRED_SLICES = frozenset(
    {
        "c1-boundary-positive",
        "c1-near-boundary-negative",
        "c2-gutter-bridge",
        "c2-ambiguous-overlap-bridge",
        "c2-pair-precedence-slot",
        "c2-one-sided-non-unique-fail-closed",
        "c2-conflict-cycle-safety",
        "c3-positive-recovery",
        "c3-zero-multiple-anchor-negative",
        "c3-zero-multiple-companion-negative",
        "c3-invalid-topology-negative",
        "c3-insufficient-visible-support-negative",
        "b1-horizontal",
        "b1-vertical",
        "b1-mixed-orientation",
        "combined-c1-c2-c3-b1",
        "clean-control",
        "intentional-fallback",
    }
)

DESIGN_REQUIREMENTS = {
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
    "c3-zero-multiple-anchor-negative": {"minPairs": 6, "minPages": 2},
    "c3-zero-multiple-companion-negative": {"minPairs": 6, "minPages": 2},
    "c3-invalid-topology-negative": {"minPairs": 6, "minPages": 2},
    "c3-insufficient-visible-support-negative": {"minPairs": 6, "minPages": 2},
    "b1-horizontal": {"minPairs": 10, "minPages": 4},
    "b1-vertical": {"minPairs": 10, "minPages": 4},
    "b1-mixed-orientation": {"minPairs": 10, "minPages": 4},
    "combined-c1-c2-c3-b1": {"minPairs": 12, "minPages": 4},
    "clean-control": {"minPairs": 16, "minPages": 6},
    "intentional-fallback": {"minPairs": 8, "minPages": 3},
}

EXERCISE_MINIMA = {
    "c1_guarded_pairs": 4,
    "c2_gutter_pairs": 3,
    "c2_overlap_pairs": 3,
    "c2_pair_precedence_pairs": 3,
    "c2_fail_closed_no_relation_pairs": 2,
    "c2_conflict_cycle_fallback_pairs": 2,
    "c3_positive_pairs": 4,
    "c3_zero_multiple_anchor_rejection_pairs": 2,
    "c3_zero_multiple_companion_rejection_pairs": 2,
    "c3_invalid_topology_rejection_pairs": 2,
    "c3_insufficient_visible_support_rejection_pairs": 2,
    "b1_horizontal_pairs": 4,
    "b1_vertical_pairs": 4,
    "b1_mixed_pairs": 4,
}


class ContractError(ValueError):
    pass


class ArmId(StrEnum):
    CONTROL = "CONTROL"
    C1_ONLY = "C1_ONLY"
    C2_ONLY = "C2_ONLY"
    C1_C2 = "C1_C2"
    C3_ONLY = "C3_ONLY"
    C1_C2_C3 = "C1_C2_C3"
    B1_ONLY = "B1_ONLY"
    C1_C2_C3_B1 = "C1_C2_C3_B1"

    @property
    def c1(self) -> bool:
        return self in {self.C1_ONLY, self.C1_C2, self.C1_C2_C3, self.C1_C2_C3_B1}

    @property
    def c2(self) -> bool:
        return self in {self.C2_ONLY, self.C1_C2, self.C1_C2_C3, self.C1_C2_C3_B1}

    @property
    def c3(self) -> bool:
        return self in {self.C3_ONLY, self.C1_C2_C3, self.C1_C2_C3_B1}

    @property
    def b1(self) -> bool:
        return self in {self.B1_ONLY, self.C1_C2_C3_B1}


@dataclass(frozen=True, slots=True)
class RegionFixture:
    region_id: str
    source_index: int
    lines: tuple[tuple[tuple[int, int], ...], ...]
    angle: float


@dataclass(frozen=True, slots=True)
class ArmPageInput:
    page_id: str
    width: int
    height: int
    regions: tuple[RegionFixture, ...]


@dataclass(frozen=True, slots=True)
class QualificationPair:
    pair_id: str
    earlier: str
    later: str
    slices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PageGroundTruth:
    page_id: str
    reading_order: tuple[str, ...]
    unscored_region_ids: tuple[str, ...]
    qualification_pairs: tuple[QualificationPair, ...]
    layout_tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusDesign:
    corpus_id: str
    version: str
    page_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    corpus_id: str
    version: str
    design_sha256: str
    inventory: tuple[tuple[str, str], ...]


def _load_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise ContractError(f"{path}: expected JSON object")
    return data


def _exact_keys(data: dict[str, Any], required: set[str], where: str) -> None:
    if set(data) != required:
        raise ContractError(
            f"{where}: property mismatch: missing={sorted(required - set(data))}, "
            f"extra={sorted(set(data) - required)}"
        )


def _page_id(value: object, where: str) -> str:
    if not isinstance(value, str) or PAGE_ID_RE.fullmatch(value) is None:
        raise ContractError(f"{where}: page ID must match QNNN")
    if value.startswith("H"):
        raise ContractError(f"{where}: historical H01-H16 identifiers are forbidden")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_arm_input(path: Path) -> ArmPageInput:
    data = _load_object(path)
    _exact_keys(data, {"schemaVersion", "pageId", "width", "height", "regions"}, str(path))
    if data["schemaVersion"] != INPUT_SCHEMA_VERSION:
        raise ContractError(f"{path}: wrong input schema")
    page_id = _page_id(data["pageId"], f"{path}.pageId")
    width, height = data["width"], data["height"]
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        raise ContractError(f"{path}: invalid dimensions")
    raw_regions = data["regions"]
    if not isinstance(raw_regions, list) or len(raw_regions) < 2:
        raise ContractError(f"{path}.regions: at least two regions required")
    regions: list[RegionFixture] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    for position, raw in enumerate(raw_regions):
        if not isinstance(raw, dict):
            raise ContractError(f"{path}.regions[{position}]: object required")
        _exact_keys(raw, {"regionId", "sourceIndex", "lines", "angle"}, f"region {position}")
        region_id = raw["regionId"]
        source_index = raw["sourceIndex"]
        if not isinstance(region_id, str) or not region_id:
            raise ContractError(f"{path}.regions[{position}].regionId: invalid")
        if not isinstance(source_index, int) or source_index < 0:
            raise ContractError(f"{path}.regions[{position}].sourceIndex: invalid")
        if region_id in seen_ids or source_index in seen_indexes:
            raise ContractError(f"{path}: duplicate region identity/index")
        seen_ids.add(region_id)
        seen_indexes.add(source_index)
        raw_lines = raw["lines"]
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ContractError(f"{path}.regions[{position}].lines: nonempty array required")
        lines: list[tuple[tuple[int, int], ...]] = []
        for line_index, raw_line in enumerate(raw_lines):
            if not isinstance(raw_line, list) or len(raw_line) != 4:
                raise ContractError(
                    f"{path}.regions[{position}].lines[{line_index}]: quadrilateral required"
                )
            points: list[tuple[int, int]] = []
            for raw_point in raw_line:
                if (
                    not isinstance(raw_point, list)
                    or len(raw_point) != 2
                    or not all(isinstance(item, int) for item in raw_point)
                ):
                    raise ContractError("line points must be integer [x,y] pairs")
                x, y = raw_point
                if not (0 <= x <= width and 0 <= y <= height):
                    raise ContractError("line point outside page")
                points.append((x, y))
            lines.append(tuple(points))
        angle = raw["angle"]
        if not isinstance(angle, int | float):
            raise ContractError(f"{path}.regions[{position}].angle: number required")
        regions.append(RegionFixture(region_id, source_index, tuple(lines), float(angle)))
    if sorted(seen_indexes) != list(range(len(seen_indexes))):
        raise ContractError(f"{path}: sourceIndex must be contiguous from zero")
    return ArmPageInput(
        page_id, width, height, tuple(sorted(regions, key=lambda r: r.source_index))
    )


def load_ground_truth(path: Path) -> PageGroundTruth:
    data = _load_object(path)
    _exact_keys(
        data,
        {
            "schemaVersion",
            "pageId",
            "readingOrder",
            "unscoredRegionIds",
            "qualificationPairs",
            "layoutTags",
        },
        str(path),
    )
    if data["schemaVersion"] != ANNOTATION_SCHEMA_VERSION:
        raise ContractError(f"{path}: wrong annotation schema")
    page_id = _page_id(data["pageId"], f"{path}.pageId")
    reading = data["readingOrder"]
    unscored = data["unscoredRegionIds"]
    tags = data["layoutTags"]
    if (
        not isinstance(reading, list)
        or not reading
        or not all(isinstance(item, str) for item in reading)
    ):
        raise ContractError(f"{path}.readingOrder: nonempty string array required")
    if len(set(reading)) != len(reading):
        raise ContractError(f"{path}.readingOrder: duplicates")
    if not isinstance(unscored, list) or not all(isinstance(item, str) for item in unscored):
        raise ContractError(f"{path}.unscoredRegionIds: string array required")
    if set(reading) & set(unscored):
        raise ContractError(f"{path}: scored/unscored overlap")
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        raise ContractError(f"{path}.layoutTags: string array required")
    position = {region_id: index for index, region_id in enumerate(reading)}
    raw_pairs = data["qualificationPairs"]
    if not isinstance(raw_pairs, list) or not raw_pairs:
        raise ContractError(f"{path}.qualificationPairs: nonempty array required")
    pairs: list[QualificationPair] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_pairs):
        if not isinstance(raw, dict):
            raise ContractError(f"{path}.qualificationPairs[{index}]: object required")
        _exact_keys(raw, {"id", "earlier", "later", "slices"}, f"pair {index}")
        pair_id, earlier, later = raw["id"], raw["earlier"], raw["later"]
        slices = raw["slices"]
        if not all(isinstance(item, str) for item in (pair_id, earlier, later)):
            raise ContractError(f"{path}.qualificationPairs[{index}]: string IDs required")
        if earlier not in position or later not in position or position[earlier] >= position[later]:
            raise ContractError(f"{path}.qualificationPairs[{index}]: must follow GT precedence")
        if (
            not isinstance(slices, list)
            or not slices
            or not all(isinstance(item, str) for item in slices)
        ):
            raise ContractError(f"{path}.qualificationPairs[{index}].slices: nonempty strings")
        normalized_slices = tuple(sorted(set(slices)))
        unknown = sorted(set(normalized_slices) - REQUIRED_SLICES)
        if unknown:
            raise ContractError(f"{path}.qualificationPairs[{index}]: unknown slices {unknown}")
        if pair_id in seen_ids or (earlier, later) in seen_pairs:
            raise ContractError(f"{path}: duplicate qualification pair")
        seen_ids.add(pair_id)
        seen_pairs.add((earlier, later))
        pairs.append(QualificationPair(pair_id, earlier, later, normalized_slices))
    return PageGroundTruth(
        page_id,
        tuple(reading),
        tuple(unscored),
        tuple(pairs),
        tuple(sorted(set(tags))),
    )


def load_corpus_design(path: Path) -> CorpusDesign:
    data = _load_object(path)
    _exact_keys(
        data,
        {
            "schemaVersion",
            "corpusId",
            "version",
            "pageIds",
            "requirements",
            "requiredSlices",
            "authorshipBoundary",
        },
        "corpus-design",
    )
    if data["schemaVersion"] != CORPUS_DESIGN_SCHEMA_VERSION:
        raise ContractError("corpus-design: wrong schema")
    corpus_id, version = data["corpusId"], data["version"]
    if (
        not isinstance(corpus_id, str)
        or not corpus_id
        or corpus_id == "mangasensei-reading-order-heldout-v2"
    ):
        raise ContractError("corpus-design: new non-historical corpusId required")
    if not isinstance(version, str) or not version:
        raise ContractError("corpus-design: version required")
    if data["authorshipBoundary"] != "new-project-authored-no-historical-v2-case-reuse":
        raise ContractError("corpus-design: authorship boundary mismatch")
    if data["requirements"] != DESIGN_REQUIREMENTS:
        raise ContractError("corpus-design: frozen requirements mismatch")
    if data["requiredSlices"] != SLICE_MINIMA:
        raise ContractError("corpus-design: frozen slice minima mismatch")
    page_ids = data["pageIds"]
    if not isinstance(page_ids, list) or not all(isinstance(item, str) for item in page_ids):
        raise ContractError("corpus-design: pageIds must be strings")
    normalized = tuple(_page_id(item, "corpus-design.pageIds") for item in page_ids)
    if len(normalized) != len(set(normalized)):
        raise ContractError("corpus-design: duplicate pageIds")
    if normalized != tuple(sorted(normalized)):
        raise ContractError("corpus-design: pageIds must be canonical sorted")
    if len(normalized) < DESIGN_REQUIREMENTS["minimumPageCount"]:
        raise ContractError("corpus-design: insufficient pages")
    return CorpusDesign(corpus_id, version, normalized)


def load_manifest(path: Path) -> CorpusManifest:
    data = _load_object(path)
    _exact_keys(
        data,
        {"schemaVersion", "corpusId", "version", "designSha256", "inventory"},
        "manifest",
    )
    if data["schemaVersion"] != CORPUS_MANIFEST_SCHEMA_VERSION:
        raise ContractError("manifest: wrong schema")
    corpus_id, version, design_sha = data["corpusId"], data["version"], data["designSha256"]
    if not isinstance(corpus_id, str) or not corpus_id:
        raise ContractError("manifest: corpusId required")
    if not isinstance(version, str) or not version:
        raise ContractError("manifest: version required")
    if not isinstance(design_sha, str) or HEX64_RE.fullmatch(design_sha) is None:
        raise ContractError("manifest: invalid designSha256")
    raw_inventory = data["inventory"]
    if not isinstance(raw_inventory, list) or not raw_inventory:
        raise ContractError("manifest: nonempty inventory required")
    inventory: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_inventory):
        if not isinstance(raw, dict) or set(raw) != {"file", "sha256"}:
            raise ContractError(f"manifest.inventory[{index}]: file/sha256 required")
        name, digest = raw["file"], raw["sha256"]
        if (
            not isinstance(name, str)
            or not name
            or name.startswith("/")
            or ".." in Path(name).parts
        ):
            raise ContractError(f"manifest.inventory[{index}]: unsafe path")
        if name.startswith("assets/reading-order-v2/heldout-v1"):
            raise ContractError("manifest: historical v2 asset reuse is forbidden")
        if not isinstance(digest, str) or HEX64_RE.fullmatch(digest) is None:
            raise ContractError(f"manifest.inventory[{index}]: invalid sha256")
        if name in seen:
            raise ContractError("manifest: duplicate inventory path")
        seen.add(name)
        inventory.append((name, digest))
    return CorpusManifest(corpus_id, version, design_sha, tuple(sorted(inventory)))


def validate_required_slice_inventory(pages: Iterable[PageGroundTruth]) -> None:
    pair_counts = {name: 0 for name in REQUIRED_SLICES}
    page_sets = {name: set() for name in REQUIRED_SLICES}
    total_pairs = 0
    scored_regions = 0
    pages_tuple = tuple(pages)
    for page in pages_tuple:
        total_pairs += len(page.qualification_pairs)
        scored_regions += len(page.reading_order)
        for pair in page.qualification_pairs:
            for name in pair.slices:
                pair_counts[name] += 1
                page_sets[name].add(page.page_id)
    if total_pairs < DESIGN_REQUIREMENTS["minimumQualificationPairs"]:
        raise ContractError("held-out corpus has too few qualification pairs")
    if scored_regions < DESIGN_REQUIREMENTS["minimumScoredRegions"]:
        raise ContractError("held-out corpus has too few scored regions")
    for name, minima in SLICE_MINIMA.items():
        if pair_counts[name] < minima["minPairs"] or len(page_sets[name]) < minima["minPages"]:
            raise ContractError(
                f"held-out corpus slice {name} below frozen minima: "
                f"pairs={pair_counts[name]}, pages={len(page_sets[name])}"
            )


def validate_corpus(
    corpus_root: Path,
) -> tuple[CorpusDesign, CorpusManifest, tuple[PageGroundTruth, ...]]:
    if corpus_root.as_posix().endswith("assets/reading-order-v2/heldout-v1"):
        raise ContractError("historical H01-H16 corpus cannot be qualified")
    design_path = corpus_root / "corpus-design.json"
    manifest_path = corpus_root / "manifest.json"
    if not design_path.is_file() or not manifest_path.is_file():
        raise ContractError("sealed corpus design/manifest are required")
    design = load_corpus_design(design_path)
    manifest = load_manifest(manifest_path)
    if manifest.corpus_id != design.corpus_id or manifest.version != design.version:
        raise ContractError("corpus design/manifest identity mismatch")
    if manifest.design_sha256 != _sha256(design_path):
        raise ContractError("manifest designSha256 mismatch")

    expected_paths: set[str] = {"corpus-design.json"}
    for page_id in design.page_ids:
        expected_paths.update(
            {
                f"images/{page_id}.png",
                f"inputs/{page_id}.json",
                f"annotations/{page_id}.json",
            }
        )
    inventory = dict(manifest.inventory)
    if set(inventory) != expected_paths:
        raise ContractError(
            "manifest inventory must contain exactly corpus-design plus "
            "image/input/annotation per page"
        )
    for relative, expected_digest in manifest.inventory:
        path = corpus_root / relative
        if not path.is_file() or _sha256(path) != expected_digest:
            raise ContractError(f"corpus inventory checksum mismatch: {relative}")

    pages: list[PageGroundTruth] = []
    for page_id in design.page_ids:
        page_input = load_arm_input(corpus_root / "inputs" / f"{page_id}.json")
        gt = load_ground_truth(corpus_root / "annotations" / f"{page_id}.json")
        if page_input.page_id != page_id or gt.page_id != page_id:
            raise ContractError(f"{page_id}: file identity mismatch")
        input_ids = {region.region_id for region in page_input.regions}
        expected_ids = set(gt.reading_order) | set(gt.unscored_region_ids)
        if input_ids != expected_ids:
            raise ContractError(f"{page_id}: region inventory differs between input and annotation")
        pages.append(gt)
    validate_required_slice_inventory(pages)
    return design, manifest, tuple(pages)


def canonical_qualification_identity(
    *,
    experiment_id: str,
    spec_sha256: str,
    manifest_sha256: str,
    design_sha256: str,
    execution_sha: str,
    execution_tree_sha: str,
) -> str:
    values = {
        "designSha256": design_sha256,
        "executionSha": execution_sha,
        "executionTreeSha": execution_tree_sha,
        "experimentId": experiment_id,
        "manifestSha256": manifest_sha256,
        "specSha256": spec_sha256,
    }
    for name in ("specSha256", "manifestSha256", "designSha256"):
        value = values[name]
        if HEX64_RE.fullmatch(value) is None:
            raise ContractError(f"{name}: invalid SHA-256")
    if HEX40_RE.fullmatch(execution_sha) is None or HEX40_RE.fullmatch(execution_tree_sha) is None:
        raise ContractError("execution SHA/tree must be 40 lowercase hex characters")
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"ropv2q-{hashlib.sha256(encoded).hexdigest()}"


def validate_qualification_identity(value: str, **parts: str) -> None:
    if QUALIFICATION_ID_RE.fullmatch(value) is None:
        raise ContractError("qualification identity format invalid")
    expected = canonical_qualification_identity(**parts)
    if value != expected:
        raise ContractError("qualification identity does not match frozen identity inputs")
