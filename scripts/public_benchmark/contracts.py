from __future__ import annotations

import hashlib
import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

OBSERVATION_SCHEMA_VERSION = "1.0.0"
OBSERVATION_KIND = "mangasensei-public-ocr-observation"
REPORT_SCHEMA_VERSION = "1.0.0"
METRIC_SPEC_VERSION = "public-ocr-benchmark-v1"
EVALUATOR_VERSION = "1.0.0"

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

JsonObject: TypeAlias = dict[str, object]


class BenchmarkContractError(ValueError):
    """Raised when a corpus or observation violates the benchmark contract."""


@dataclass(frozen=True, slots=True)
class BBox:
    x: int
    y: int
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class GroundTruthRegion:
    id: str
    bbox: BBox
    polygon: tuple[tuple[int, int], ...]
    transcription_raw: str
    text_role: str
    text_form: str
    detection_scored: bool
    recognition_scored: bool
    reading_order_scored: bool
    reading_order_position: int | None


@dataclass(frozen=True, slots=True)
class NegativeZone:
    id: str
    kind: str
    bbox: BBox
    polygon: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class GroundTruthPage:
    id: str
    width: int
    height: int
    image_sha256: str
    annotation_sha256: str
    regions: tuple[GroundTruthRegion, ...]
    furigana_relation_ids: tuple[str, ...]
    presentation_mark_ids: tuple[str, ...]
    negative_zones: tuple[NegativeZone, ...]
    reading_order_sequence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusBundle:
    root: Path
    corpus_id: str
    schema_version: int
    manifest_sha256: str
    annotation_schema_sha256: str
    pages: tuple[GroundTruthPage, ...]


@dataclass(frozen=True, slots=True)
class ObservedRegion:
    id: str
    bbox: BBox
    polygon: tuple[tuple[int, int], ...] | None
    angle: float
    confidence: float
    text: str
    reading_order: int


@dataclass(frozen=True, slots=True)
class ObservationPage:
    id: str
    image_sha256: str
    annotation_sha256: str
    width: int
    height: int
    regions: tuple[ObservedRegion, ...]


@dataclass(frozen=True, slots=True)
class Observation:
    path: Path
    sha256: str
    schema_version: str
    kind: str
    corpus_id: str
    corpus_schema_version: int
    manifest_sha256: str
    annotation_schema_sha256: str
    producer: JsonObject
    features: JsonObject
    ocr: JsonObject
    runtime: JsonObject
    pages: tuple[ObservationPage, ...]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _error(location: str, message: str) -> BenchmarkContractError:
    return BenchmarkContractError(f"{location}: {message}")


def _object(value: object, location: str) -> JsonObject:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _error(location, "expected object")
    return dict(value)


def _array(value: object, location: str) -> list[object]:
    if not isinstance(value, list):
        raise _error(location, "expected array")
    return value


def _string(value: object, location: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise _error(location, "expected string")
    if nonempty and not value:
        raise _error(location, "must not be empty")
    return value


def _integer(value: object, location: str, *, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _error(location, "expected integer")
    if minimum is not None and value < minimum:
        raise _error(location, f"must be >= {minimum}")
    return value


def _boolean(value: object, location: str) -> bool:
    if not isinstance(value, bool):
        raise _error(location, "expected boolean")
    return value


def _number(value: object, location: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise _error(location, "expected number")
    result = float(value)
    if not math.isfinite(result):
        raise _error(location, "must be finite")
    return result


def _exact_keys(value: JsonObject, expected: set[str], location: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise _error(location, f"property mismatch: missing={missing}, extra={extra}")


def _allowed_keys(value: JsonObject, required: set[str], optional: set[str], location: str) -> None:
    actual = set(value)
    missing = sorted(required - actual)
    extra = sorted(actual - required - optional)
    if missing or extra:
        raise _error(location, f"property mismatch: missing={missing}, extra={extra}")


def _hex(value: object, location: str, pattern: re.Pattern[str]) -> str:
    text = _string(value, location)
    if pattern.fullmatch(text) is None:
        raise _error(location, "invalid lowercase hexadecimal digest")
    return text


def _bbox(value: object, location: str, width: int, height: int) -> BBox:
    data = _object(value, location)
    _exact_keys(data, {"x", "y", "width", "height"}, location)
    x = _integer(data["x"], f"{location}.x", minimum=0)
    y = _integer(data["y"], f"{location}.y", minimum=0)
    box_width = _integer(data["width"], f"{location}.width", minimum=1)
    box_height = _integer(data["height"], f"{location}.height", minimum=1)
    if x + box_width > width or y + box_height > height:
        raise _error(location, "bbox is outside page dimensions")
    return BBox(x=x, y=y, width=box_width, height=box_height)


def _polygon(
    value: object,
    location: str,
    width: int,
    height: int,
    *,
    nullable: bool,
    min_points: int = 3,
) -> tuple[tuple[int, int], ...] | None:
    if value is None:
        if nullable:
            return None
        raise _error(location, "polygon must not be null")
    points = _array(value, location)
    if len(points) < min_points:
        raise _error(location, f"polygon must contain at least {min_points} points")
    result: list[tuple[int, int]] = []
    for index, point_value in enumerate(points):
        point = _array(point_value, f"{location}[{index}]")
        if len(point) != 2:
            raise _error(f"{location}[{index}]", "point must have two coordinates")
        x = _integer(point[0], f"{location}[{index}][0]", minimum=0)
        y = _integer(point[1], f"{location}[{index}][1]", minimum=0)
        if x > width or y > height:
            raise _error(f"{location}[{index}]", "point is outside page dimensions")
        result.append((x, y))
    return tuple(result)


def _read_json_bytes(data: bytes, location: str) -> object:
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error(location, f"invalid UTF-8 JSON: {exc}") from exc


def _corpus_file(root: Path, relative_path: str, location: str) -> Path:
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise _error(location, "path escapes corpus root") from exc
    if not candidate.is_file():
        raise _error(location, "referenced corpus file does not exist")
    return candidate


def _png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise _error(path.as_posix(), "expected PNG with IHDR header")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise _error(path.as_posix(), "invalid PNG dimensions")
    return width, height


def _manifest_inventory_digest(manifest: JsonObject, relative_path: str) -> str:
    inventory = _array(manifest.get("inventory"), "manifest.inventory")
    matches: list[str] = []
    for index, entry_value in enumerate(inventory):
        entry = _object(entry_value, f"manifest.inventory[{index}]")
        if entry.get("file") == relative_path:
            matches.append(_hex(entry.get("sha256"), f"manifest.inventory[{index}].sha256", _HEX64))
    if len(matches) != 1:
        raise _error("manifest.inventory", f"expected exactly one entry for {relative_path!r}")
    return matches[0]


def bind_observation(corpus: CorpusBundle, observation: Observation) -> None:
    if observation.corpus_id != corpus.corpus_id:
        raise _error("observation.corpus.id", "does not match corpus")
    if observation.corpus_schema_version != corpus.schema_version:
        raise _error("observation.corpus.schemaVersion", "does not match corpus manifest")
    if observation.manifest_sha256 != corpus.manifest_sha256:
        raise _error("observation.corpus.manifestSha256", "does not match raw manifest.json bytes")
    if observation.annotation_schema_sha256 != corpus.annotation_schema_sha256:
        raise _error(
            "observation.corpus.annotationSchemaSha256",
            "does not match raw annotation schema bytes",
        )

    expected_ids = tuple(page.id for page in corpus.pages)
    observed_by_id = {page.id: page for page in observation.pages}
    if set(observed_by_id) != set(expected_ids) or len(observation.pages) != len(expected_ids):
        raise _error(
            "observation.pages",
            (
                f"page inventory mismatch: expected={list(expected_ids)}, "
                f"actual={sorted(observed_by_id)}"
            ),
        )
    for page in corpus.pages:
        observed = observed_by_id[page.id]
        if observed.image_sha256 != page.image_sha256:
            raise _error(f"observation.pages[{page.id}].imageSha256", "does not match corpus")
        if observed.annotation_sha256 != page.annotation_sha256:
            raise _error(f"observation.pages[{page.id}].annotationSha256", "does not match corpus")
        if (observed.width, observed.height) != (page.width, page.height):
            raise _error(f"observation.pages[{page.id}]", "dimensions do not match corpus")
