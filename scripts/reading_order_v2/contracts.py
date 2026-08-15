from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.public_benchmark.contracts import BBox

CORPUS_ID = "mangasensei-reading-order-heldout-v2"
CORPUS_VERSION = "1.0.0"
PAGE_IDS = tuple(f"H{index:02d}" for index in range(1, 17))
PAGE_ID_RE = re.compile(r"^H(?:0[1-9]|1[0-6])$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_SLICES = {
    "A",
    "B",
    "A+B",
    "clean-control",
    "vertical-only",
    "horizontal-only",
    "mixed",
    "partial-assignment",
    "intentional-fallback",
}


class ContractError(ValueError):
    pass


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
class PanelGroundTruth:
    panel_id: str
    bbox: BBox
    precedence_position: int | None


@dataclass(frozen=True, slots=True)
class PageGroundTruth:
    page_id: str
    reading_order: tuple[str, ...]
    unscored_region_ids: tuple[str, ...]
    qualification_pairs: tuple[QualificationPair, ...]
    layout_tags: tuple[str, ...]
    panels: tuple[PanelGroundTruth, ...]


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContractError(f"{path}: expected JSON object")
    return value


def _exact_keys(data: dict[str, Any], required: set[str], where: str) -> None:
    if set(data) != required:
        raise ContractError(
            f"{where}: property mismatch: missing={sorted(required-set(data))}, "
            f"extra={sorted(set(data)-required)}"
        )


def _page_id(value: object, where: str) -> str:
    if not isinstance(value, str) or PAGE_ID_RE.fullmatch(value) is None:
        raise ContractError(f"{where}: invalid held-out page ID")
    return value


def load_arm_input(path: Path) -> ArmPageInput:
    data = _load_object(path)
    _exact_keys(data, {"schemaVersion", "pageId", "width", "height", "regions"}, str(path))
    if data["schemaVersion"] != "reading-order-v2-input-v1":
        raise ContractError(f"{path}: bad input schema version")
    page_id = _page_id(data["pageId"], f"{path}.pageId")
    width = data["width"]
    height = data["height"]
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        raise ContractError(f"{path}: invalid page dimensions")
    raw_regions = data["regions"]
    if not isinstance(raw_regions, list):
        raise ContractError(f"{path}.regions: expected array")
    regions: list[RegionFixture] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    for position, value in enumerate(raw_regions):
        if not isinstance(value, dict):
            raise ContractError(f"{path}.regions[{position}]: expected object")
        _exact_keys(value, {"regionId", "sourceIndex", "lines", "angle"}, f"region {position}")
        region_id = value["regionId"]
        source_index = value["sourceIndex"]
        if not isinstance(region_id, str) or not region_id:
            raise ContractError(f"{path}.regions[{position}].regionId: invalid")
        if not isinstance(source_index, int) or source_index < 0:
            raise ContractError(f"{path}.regions[{position}].sourceIndex: invalid")
        if region_id in seen_ids or source_index in seen_indexes:
            raise ContractError(f"{path}: duplicate region identity/index")
        seen_ids.add(region_id)
        seen_indexes.add(source_index)
        raw_lines = value["lines"]
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ContractError(f"{path}.regions[{position}].lines: expected nonempty array")
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
        angle = value["angle"]
        if not isinstance(angle, int | float):
            raise ContractError(f"{path}.regions[{position}].angle: number required")
        regions.append(RegionFixture(region_id, source_index, tuple(lines), float(angle)))
    if sorted(seen_indexes) != list(range(len(seen_indexes))):
        raise ContractError(f"{path}: sourceIndex must be contiguous from zero")
    ordered_regions = tuple(sorted(regions, key=lambda item: item.source_index))
    return ArmPageInput(page_id, width, height, ordered_regions)


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
            "panels",
            "orientationExpectations",
            "assignmentExpectations",
        },
        str(path),
    )
    if data["schemaVersion"] != "reading-order-v2-annotation-v1":
        raise ContractError(f"{path}: bad annotation schema version")
    page_id = _page_id(data["pageId"], f"{path}.pageId")
    reading = data["readingOrder"]
    unscored = data["unscoredRegionIds"]
    tags = data["layoutTags"]
    if not isinstance(reading, list) or not all(isinstance(item, str) for item in reading):
        raise ContractError(f"{path}.readingOrder: string array required")
    if len(set(reading)) != len(reading):
        raise ContractError(f"{path}.readingOrder: duplicate scored region ID")
    if not isinstance(unscored, list) or not all(isinstance(item, str) for item in unscored):
        raise ContractError(f"{path}.unscoredRegionIds: string array required")
    if set(reading) & set(unscored):
        raise ContractError(f"{path}: scored and unscored IDs overlap")
    if not isinstance(tags, list) or not all(isinstance(item, str) for item in tags):
        raise ContractError(f"{path}.layoutTags: string array required")
    position = {region_id: index for index, region_id in enumerate(reading)}
    pairs_raw = data["qualificationPairs"]
    if not isinstance(pairs_raw, list):
        raise ContractError(f"{path}.qualificationPairs: array required")
    pairs: list[QualificationPair] = []
    seen_pair_ids: set[str] = set()
    seen_pair_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(pairs_raw):
        if not isinstance(raw, dict):
            raise ContractError(f"{path}.qualificationPairs[{index}]: object required")
        _exact_keys(raw, {"id", "earlier", "later", "slices"}, f"pair {index}")
        pair_id, earlier, later, slices = raw["id"], raw["earlier"], raw["later"], raw["slices"]
        if not all(isinstance(item, str) for item in (pair_id, earlier, later)):
            raise ContractError(f"{path}.qualificationPairs[{index}]: string IDs required")
        if earlier not in position or later not in position or position[earlier] >= position[later]:
            raise ContractError(f"{path}.qualificationPairs[{index}]: pair must follow GT order")
        if (
            not isinstance(slices, list)
            or not slices
            or not all(isinstance(item, str) for item in slices)
        ):
            raise ContractError(
                f"{path}.qualificationPairs[{index}].slices: nonempty string array required"
            )
        if pair_id in seen_pair_ids or (earlier, later) in seen_pair_keys:
            raise ContractError(f"{path}: duplicate qualification pair")
        seen_pair_ids.add(pair_id)
        seen_pair_keys.add((earlier, later))
        pairs.append(QualificationPair(pair_id, earlier, later, tuple(sorted(set(slices)))))
    panels_raw = data["panels"]
    if not isinstance(panels_raw, list):
        raise ContractError(f"{path}.panels: array required")
    panels: list[PanelGroundTruth] = []
    for index, raw in enumerate(panels_raw):
        if not isinstance(raw, dict):
            raise ContractError(f"{path}.panels[{index}]: object required")
        _exact_keys(raw, {"id", "bbox", "precedencePosition"}, f"panel {index}")
        bbox = raw["bbox"]
        if not isinstance(raw["id"], str) or not isinstance(bbox, dict):
            raise ContractError(f"{path}.panels[{index}]: invalid panel")
        _exact_keys(bbox, {"x", "y", "width", "height"}, f"panel {index}.bbox")
        values = tuple(bbox[key] for key in ("x", "y", "width", "height"))
        if (
            not all(isinstance(value, int) for value in values)
            or min(values) < 0
            or values[2] <= 0
            or values[3] <= 0
        ):
            raise ContractError(f"{path}.panels[{index}].bbox: invalid")
        precedence = raw["precedencePosition"]
        if precedence is not None and (not isinstance(precedence, int) or precedence < 0):
            raise ContractError(f"{path}.panels[{index}].precedencePosition: invalid")
        panels.append(PanelGroundTruth(raw["id"], BBox(*values), precedence))
    return PageGroundTruth(
        page_id,
        tuple(reading),
        tuple(unscored),
        tuple(pairs),
        tuple(sorted(set(tags))),
        tuple(panels),
    )


def validate_corpus_design(data: dict[str, Any]) -> None:
    _exact_keys(
        data,
        {"schemaVersion", "corpusId", "version", "pageCount", "requirements", "slots"},
        "corpus-design",
    )
    if data["schemaVersion"] != "reading-order-v2-corpus-design-v1":
        raise ContractError("corpus-design: wrong schema version")
    if data["corpusId"] != CORPUS_ID or data["version"] != CORPUS_VERSION:
        raise ContractError("corpus-design: wrong corpus identity")
    if data["pageCount"] != 16:
        raise ContractError("corpus-design: pageCount must be exactly 16")
    slots = data["slots"]
    slot_ids = (
        [slot.get("id") for slot in slots if isinstance(slot, dict)]
        if isinstance(slots, list)
        else []
    )
    if slot_ids != list(PAGE_IDS):
        raise ContractError("corpus-design: slots must be exactly ordered H01..H16")
    requirements = data["requirements"]
    expected = {
        "minAQualificationPairs": 12,
        "minAPages": 5,
        "minBQualificationPairs": 12,
        "minBPages": 5,
        "minCleanOrdinaryControls": 4,
        "minIntentionalWholePageFallbackPages": 2,
        "minOpenOrIncompleteFramePages": 2,
    }
    if requirements != expected:
        raise ContractError("corpus-design: frozen minima do not match spec v1")


def arm_asset_paths(corpus_root: Path, page_id: str) -> tuple[Path, Path]:
    safe_id = _page_id(page_id, "pageId")
    return corpus_root / "images" / f"{safe_id}.png", corpus_root / "inputs" / f"{safe_id}.json"
