from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.public_benchmark.contracts import BBox

CORPUS_ID = "mangasensei-reading-order-heldout-v2"
QUALIFICATION_VERSION = "1.0.0"
DESIGN_VERSION = "corpus-design-v1"
PAGE_IDS = tuple(f"H{index:02d}" for index in range(1, 17))
REQUIRED_PAIR_SLICES = (
    "A",
    "B",
    "A+B",
    "control",
    "clean-control",
    "vertical-only",
    "horizontal-only",
    "mixed",
    "partial-assignment",
    "intentional-fallback",
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReadingOrderV2ContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QualificationPair:
    pair_id: str
    before_region_id: str
    after_region_id: str
    slices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegionGroundTruth:
    region_id: str
    scored: bool
    reading_order_position: int | None
    orientation: str
    assignment_expectation: str
    panel_id: str | None


@dataclass(frozen=True, slots=True)
class PanelGroundTruth:
    panel_id: str
    bbox: BBox
    scored: bool


@dataclass(frozen=True, slots=True)
class AnnotationPage:
    page_id: str
    width: int
    height: int
    image_sha256: str
    regions: tuple[RegionGroundTruth, ...]
    reading_order_sequence: tuple[str, ...]
    qualification_pairs: tuple[QualificationPair, ...]
    layout_tags: tuple[str, ...]
    panels: tuple[PanelGroundTruth, ...]
    panel_precedence: tuple[tuple[str, str], ...]

    @property
    def known_region_ids(self) -> tuple[str, ...]:
        return tuple(region.region_id for region in self.regions)


@dataclass(frozen=True, slots=True)
class CorpusPageManifest:
    page_id: str
    source_file: str
    source_sha256: str
    image_file: str
    image_sha256: str
    input_file: str
    input_sha256: str
    annotation_file: str
    annotation_sha256: str


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    corpus_id: str
    qualification_version: str
    design_sha256: str
    pages: tuple[CorpusPageManifest, ...]


def _object(value: object, location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReadingOrderV2ContractError(f"{location}: expected object")
    return dict(value)


def _array(value: object, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReadingOrderV2ContractError(f"{location}: expected array")
    return list(value)


def _string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReadingOrderV2ContractError(f"{location}: expected non-empty string")
    return value


def _int(value: object, location: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ReadingOrderV2ContractError(f"{location}: expected integer >= {minimum}")
    return value


def _bool(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        raise ReadingOrderV2ContractError(f"{location}: expected boolean")
    return value


def _hex64(value: object, location: str) -> str:
    text = _string(value, location)
    if _HEX64.fullmatch(text) is None:
        raise ReadingOrderV2ContractError(f"{location}: expected lowercase SHA-256")
    return text


def load_annotation(path: Path) -> AnnotationPage:
    raw = _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    if raw.get("schemaVersion") != "reading-order-v2-annotation-v1":
        raise ReadingOrderV2ContractError(f"{path}: wrong annotation schema version")
    page = _object(raw.get("page"), f"{path}.page")
    page_id = _string(page.get("id"), f"{path}.page.id")
    width = _int(page.get("width"), f"{path}.page.width", minimum=1)
    height = _int(page.get("height"), f"{path}.page.height", minimum=1)
    image_sha256 = _hex64(page.get("imageSha256"), f"{path}.page.imageSha256")

    region_values = _array(raw.get("regions"), f"{path}.regions")
    regions: list[RegionGroundTruth] = []
    for index, value in enumerate(region_values):
        item = _object(value, f"{path}.regions[{index}]")
        scored = _bool(item.get("scored"), f"{path}.regions[{index}].scored")
        position_value = item.get("readingOrderPosition")
        position = None if position_value is None else _int(
            position_value, f"{path}.regions[{index}].readingOrderPosition"
        )
        orientation = _string(item.get("orientation"), f"{path}.regions[{index}].orientation")
        if orientation not in {"horizontal", "vertical", "ambiguous"}:
            raise ReadingOrderV2ContractError(f"{path}.regions[{index}]: bad orientation")
        assignment = _string(
            item.get("assignmentExpectation"),
            f"{path}.regions[{index}].assignmentExpectation",
        )
        if assignment not in {"unique", "outside", "ambiguous", "not-applicable"}:
            raise ReadingOrderV2ContractError(
                f"{path}.regions[{index}]: bad assignment expectation"
            )
        panel_id_value = item.get("panelId")
        panel_id = None if panel_id_value is None else _string(
            panel_id_value, f"{path}.regions[{index}].panelId"
        )
        regions.append(
            RegionGroundTruth(
                region_id=_string(item.get("id"), f"{path}.regions[{index}].id"),
                scored=scored,
                reading_order_position=position,
                orientation=orientation,
                assignment_expectation=assignment,
                panel_id=panel_id,
            )
        )
    ids = [region.region_id for region in regions]
    if len(ids) != len(set(ids)):
        raise ReadingOrderV2ContractError(f"{path}: duplicate region IDs")
    scored = [region for region in regions if region.scored]
    positions = [region.reading_order_position for region in scored]
    if any(position is None for position in positions):
        raise ReadingOrderV2ContractError(f"{path}: scored region missing reading-order position")
    if sorted(position for position in positions if position is not None) != list(
        range(len(scored))
    ):
        raise ReadingOrderV2ContractError(f"{path}: scored positions must be contiguous from zero")
    if any(region.reading_order_position is not None for region in regions if not region.scored):
        raise ReadingOrderV2ContractError(f"{path}: unscored region has reading-order position")

    sequence = tuple(
        _string(value, f"{path}.readingOrderSequence")
        for value in _array(raw.get("readingOrderSequence"), f"{path}.readingOrderSequence")
    )
    expected_sequence = tuple(
        region.region_id
        for region in sorted(scored, key=lambda item: item.reading_order_position or 0)
    )
    if sequence != expected_sequence:
        raise ReadingOrderV2ContractError(
            f"{path}: reading-order sequence disagrees with positions"
        )

    pair_values = _array(raw.get("qualificationPairs"), f"{path}.qualificationPairs")
    pairs: list[QualificationPair] = []
    pair_ids: set[str] = set()
    pair_keys: set[tuple[str, str]] = set()
    position_by_id = {region_id: index for index, region_id in enumerate(sequence)}
    for index, value in enumerate(pair_values):
        item = _object(value, f"{path}.qualificationPairs[{index}]")
        pair_id = _string(item.get("id"), f"{path}.qualificationPairs[{index}].id")
        before = _string(item.get("before"), f"{path}.qualificationPairs[{index}].before")
        after = _string(item.get("after"), f"{path}.qualificationPairs[{index}].after")
        slices = tuple(
            _string(slice_value, f"{path}.qualificationPairs[{index}].slices")
            for slice_value in _array(
                item.get("slices"), f"{path}.qualificationPairs[{index}].slices"
            )
        )
        if pair_id in pair_ids or (before, after) in pair_keys:
            raise ReadingOrderV2ContractError(f"{path}: duplicate qualification pair")
        if before == after or before not in position_by_id or after not in position_by_id:
            raise ReadingOrderV2ContractError(f"{path}: invalid qualification-pair regions")
        if position_by_id[before] >= position_by_id[after]:
            raise ReadingOrderV2ContractError(f"{path}: qualification pair reverses GT order")
        if not slices or any(slice_name not in REQUIRED_PAIR_SLICES for slice_name in slices):
            raise ReadingOrderV2ContractError(f"{path}: invalid qualification-pair slice")
        if len(set(slices)) != len(slices):
            raise ReadingOrderV2ContractError(f"{path}: duplicate qualification-pair slice")
        pair_ids.add(pair_id)
        pair_keys.add((before, after))
        pairs.append(QualificationPair(pair_id, before, after, slices))

    layout_tags = tuple(
        sorted(
            _string(value, f"{path}.layoutTags")
            for value in _array(raw.get("layoutTags"), f"{path}.layoutTags")
        )
    )
    panel_values = _array(raw.get("panels"), f"{path}.panels")
    panels: list[PanelGroundTruth] = []
    panel_ids: set[str] = set()
    for index, value in enumerate(panel_values):
        item = _object(value, f"{path}.panels[{index}]")
        panel_id = _string(item.get("id"), f"{path}.panels[{index}].id")
        bbox_value = _object(item.get("bbox"), f"{path}.panels[{index}].bbox")
        bbox = BBox(
            x=_int(bbox_value.get("x"), f"{path}.panels[{index}].bbox.x"),
            y=_int(bbox_value.get("y"), f"{path}.panels[{index}].bbox.y"),
            width=_int(bbox_value.get("width"), f"{path}.panels[{index}].bbox.width", minimum=1),
            height=_int(bbox_value.get("height"), f"{path}.panels[{index}].bbox.height", minimum=1),
        )
        if bbox.x + bbox.width > width or bbox.y + bbox.height > height:
            raise ReadingOrderV2ContractError(f"{path}: panel bbox outside page")
        if panel_id in panel_ids:
            raise ReadingOrderV2ContractError(f"{path}: duplicate panel ID")
        panel_ids.add(panel_id)
        panels.append(
            PanelGroundTruth(
                panel_id,
                bbox,
                _bool(item.get("scored"), f"{path}.panels[{index}].scored"),
            )
        )

    for region in regions:
        if region.panel_id is not None and region.panel_id not in panel_ids:
            raise ReadingOrderV2ContractError(
                f"{path}: region {region.region_id} references unknown panel {region.panel_id}"
            )

    precedence_values = _array(raw.get("panelPrecedence"), f"{path}.panelPrecedence")
    precedence: list[tuple[str, str]] = []
    for index, value in enumerate(precedence_values):
        item = _array(value, f"{path}.panelPrecedence[{index}]")
        if len(item) != 2:
            raise ReadingOrderV2ContractError(f"{path}: panel precedence pair must have two IDs")
        before = _string(item[0], f"{path}.panelPrecedence[{index}][0]")
        after = _string(item[1], f"{path}.panelPrecedence[{index}][1]")
        if before == after or before not in panel_ids or after not in panel_ids:
            raise ReadingOrderV2ContractError(f"{path}: invalid panel precedence")
        precedence.append((before, after))
    if len(set(precedence)) != len(precedence):
        raise ReadingOrderV2ContractError(f"{path}: duplicate panel precedence")

    return AnnotationPage(
        page_id=page_id,
        width=width,
        height=height,
        image_sha256=image_sha256,
        regions=tuple(regions),
        reading_order_sequence=sequence,
        qualification_pairs=tuple(pairs),
        layout_tags=layout_tags,
        panels=tuple(panels),
        panel_precedence=tuple(precedence),
    )


def load_manifest(path: Path) -> CorpusManifest:
    raw = _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    if raw.get("schemaVersion") != "reading-order-v2-manifest-v1":
        raise ReadingOrderV2ContractError(f"{path}: wrong manifest schema version")
    corpus_id = _string(raw.get("corpusId"), f"{path}.corpusId")
    qualification_version = _string(
        raw.get("qualificationVersion"), f"{path}.qualificationVersion"
    )
    if corpus_id != CORPUS_ID or qualification_version != QUALIFICATION_VERSION:
        raise ReadingOrderV2ContractError(f"{path}: wrong frozen corpus identity")
    design_sha256 = _hex64(raw.get("designSha256"), f"{path}.designSha256")
    page_values = _array(raw.get("pages"), f"{path}.pages")
    pages: list[CorpusPageManifest] = []
    for index, value in enumerate(page_values):
        item = _object(value, f"{path}.pages[{index}]")
        page_id = _string(item.get("id"), f"{path}.pages[{index}].id")
        entries: dict[str, tuple[str, str]] = {}
        for key in ("source", "image", "input", "annotation"):
            entry = _object(item.get(key), f"{path}.pages[{index}].{key}")
            entries[key] = (
                _string(entry.get("file"), f"{path}.pages[{index}].{key}.file"),
                _hex64(entry.get("sha256"), f"{path}.pages[{index}].{key}.sha256"),
            )
        pages.append(
            CorpusPageManifest(
                page_id=page_id,
                source_file=entries["source"][0],
                source_sha256=entries["source"][1],
                image_file=entries["image"][0],
                image_sha256=entries["image"][1],
                input_file=entries["input"][0],
                input_sha256=entries["input"][1],
                annotation_file=entries["annotation"][0],
                annotation_sha256=entries["annotation"][1],
            )
        )
    if tuple(page.page_id for page in pages) != PAGE_IDS:
        raise ReadingOrderV2ContractError(f"{path}: manifest must contain exactly H01..H16")
    return CorpusManifest(corpus_id, qualification_version, design_sha256, tuple(pages))
