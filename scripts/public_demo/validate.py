from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "assets" / "public-demo"
MANIFEST_PATH = CORPUS / "manifest.json"
SCHEMA_PATH = CORPUS / "annotations" / "schema-v1.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_ORIENTATIONS = {"vertical-rl", "horizontal-ltr", "angled", "mixed"}
ALLOWED_ROLES = {"dialogue", "thought", "narration", "environmental", "sfx", "uncertain"}
ALLOWED_FORMS = {"base", "ruby"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fail(message: str) -> None:
    raise AssertionError(message)


def _resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        fail(f"unsupported external JSON Schema reference: {reference}")
    value: Any = root_schema
    for part in reference[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        value = value[part]
    if not isinstance(value, dict):
        fail(f"JSON Schema reference does not resolve to an object: {reference}")
    return value


def _matches_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    fail(f"unsupported JSON Schema type in v1 validator: {expected}")
    return False


def validate_json_schema(
    value: object,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    location: str,
) -> None:
    if "$ref" in schema:
        validate_json_schema(
            value, _resolve_ref(root_schema, schema["$ref"]), root_schema, location
        )
        return
    if "const" in schema and value != schema["const"]:
        fail(f"{location}: expected constant {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        fail(f"{location}: value {value!r} is not in enum {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(_matches_type(value, item) for item in allowed):
            fail(f"{location}: expected JSON Schema type {allowed!r}, got {type(value).__name__}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            fail(f"{location}: missing required properties {missing!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                fail(f"{location}: unexpected properties {extra!r}")
        for key, child in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                validate_json_schema(child, child_schema, root_schema, f"{location}.{key}")

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        maximum_items = schema.get("maxItems")
        if minimum_items is not None and len(value) < minimum_items:
            fail(f"{location}: array has fewer than {minimum_items} items")
        if maximum_items is not None and len(value) > maximum_items:
            fail(f"{location}: array has more than {maximum_items} items")
        prefix_items = schema.get("prefixItems", [])
        for index, child_schema in enumerate(prefix_items):
            if index < len(value):
                validate_json_schema(
                    value[index], child_schema, root_schema, f"{location}[{index}]"
                )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            start = len(prefix_items) if prefix_items else 0
            for index, child in enumerate(value[start:], start=start):
                validate_json_schema(
                    child, item_schema, root_schema, f"{location}[{index}]"
                )

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if minimum_length is not None and len(value) < minimum_length:
            fail(f"{location}: string is shorter than {minimum_length}")
        pattern = schema.get("pattern")
        if pattern is not None and re.fullmatch(pattern, value) is None:
            fail(f"{location}: string {value!r} does not match {pattern!r}")

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            fail(f"{location}: integer {value} is below minimum {minimum}")
        if maximum is not None and value > maximum:
            fail(f"{location}: integer {value} is above maximum {maximum}")


def geometry_in_bounds(
    geometry: dict[str, Any], width: int, height: int, label: str
) -> None:
    bbox = geometry["bbox"]
    x, y, box_width, box_height = (
        bbox[key] for key in ("x", "y", "width", "height")
    )
    if (
        min(x, y, box_width, box_height) < 0
        or box_width == 0
        or box_height == 0
        or x + box_width > width
        or y + box_height > height
    ):
        fail(f"{label}: bbox out of bounds: {bbox}")
    polygon = geometry["polygon"]
    if len(polygon) < 4:
        fail(f"{label}: polygon needs at least four points")
    for px, py in polygon:
        if not (0 <= px <= width and 0 <= py <= height):
            fail(f"{label}: polygon point out of bounds: {(px, py)}")


def validate_annotation(
    path: Path,
    source_svg: str,
    schema: dict[str, Any],
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_json_schema(data, schema, schema, path.name)
    page = data["page"]
    width, height = page["width"], page["height"]
    if (width, height) != (1440, 2048):
        fail(f"{path}: non-canonical dimensions")
    if not HEX64.fullmatch(page["imageSha256"]):
        fail(f"{path}: imageSha256 is not frozen")

    ids: set[str] = set()
    regions = data["regions"]
    by_id = {region["id"]: region for region in regions}
    if len(by_id) != len(regions):
        fail(f"{path}: duplicate region id")
    for region in regions:
        region_id = region["id"]
        if region_id in ids:
            fail(f"{path}: duplicate id {region_id}")
        ids.add(region_id)
        if f'data-region-id="{region_id}"' not in source_svg:
            fail(f"{path}: source SVG is missing region marker {region_id}")
        geometry_in_bounds(region["geometry"], width, height, region_id)
        if region["orientation"] not in ALLOWED_ORIENTATIONS:
            fail(f"{path}: bad orientation {region_id}")
        if region["textRole"] not in ALLOWED_ROLES:
            fail(f"{path}: bad textRole {region_id}")
        if region["textForm"] not in ALLOWED_FORMS:
            fail(f"{path}: bad textForm {region_id}")
        if region["readingOrder"]["scored"] != region["scoring"]["readingOrder"]:
            fail(f"{path}: reading-order scoring flags disagree for {region_id}")

    scored = [region for region in regions if region["readingOrder"]["scored"]]
    positions = [region["readingOrder"]["position"] for region in scored]
    if sorted(positions) != list(range(len(scored))):
        fail(f"{path}: scored reading-order positions must be contiguous from zero")
    unscored = [region for region in regions if not region["readingOrder"]["scored"]]
    if any(region["readingOrder"]["position"] is not None for region in unscored):
        fail(f"{path}: unscored regions must use null reading-order position")
    sequence = data["readingOrderContract"]["sequence"]
    expected_sequence = [
        region["id"]
        for region in sorted(scored, key=lambda item: item["readingOrder"]["position"])
    ]
    if sequence != expected_sequence:
        fail(f"{path}: reading-order sequence disagrees with region positions")

    relation_ids: set[str] = set()
    for relation in data["furiganaRelations"]:
        if relation["id"] in relation_ids:
            fail(f"{path}: duplicate furigana relation id {relation['id']}")
        relation_ids.add(relation["id"])
        ruby = by_id.get(relation["rubyRegionId"])
        base = by_id.get(relation["baseRegionId"])
        if (
            ruby is None
            or base is None
            or ruby["textForm"] != "ruby"
            or base["textForm"] != "base"
        ):
            fail(f"{path}: invalid furigana relation {relation['id']}")
        start = relation["baseTextSpan"]["start"]
        end = relation["baseTextSpan"]["end"]
        if not (0 <= start < end <= len(base["transcription"]["raw"])):
            fail(f"{path}: invalid furigana base span {relation['id']}")
        if relation["reading"] != ruby["transcription"]["raw"]:
            fail(f"{path}: furigana reading differs from ruby transcription {relation['id']}")

    presentation_ids: set[str] = set()
    for mark in data["presentationMarks"]:
        mark_id = mark["id"]
        if mark_id in presentation_ids or mark_id in ids:
            fail(f"{path}: duplicate presentation mark id {mark_id}")
        presentation_ids.add(mark_id)
        base = by_id.get(mark["associatedRegionId"])
        if base is None or base["textForm"] != "base":
            fail(f"{path}: presentation mark missing base region {mark_id}")
        start = mark["baseTextSpan"]["start"]
        end = mark["baseTextSpan"]["end"]
        if not (0 <= start < end <= len(base["transcription"]["raw"])):
            fail(f"{path}: invalid presentation base span {mark_id}")
        if mark["transcriptionEffect"] != "none":
            fail(f"{path}: presentation marks must not mutate lexical transcription")
        if f'data-presentation-id="{mark_id}"' not in source_svg:
            fail(f"{path}: source SVG missing presentation mark {mark_id}")
        geometry_in_bounds(mark["geometry"], width, height, mark_id)

    negative_ids: set[str] = set()
    for zone in data["negativeZones"]:
        zone_id = zone["id"]
        if zone_id in ids or zone_id in presentation_ids or zone_id in negative_ids:
            fail(f"{path}: negative-zone id collides with another annotation id")
        negative_ids.add(zone_id)
        if zone["expected"] != "no-text-region":
            fail(f"{path}: invalid negative-zone expectation")
        if f'data-negative-id="{zone_id}"' not in source_svg:
            fail(f"{path}: source SVG missing negative zone {zone_id}")
        geometry_in_bounds(zone["geometry"], width, height, zone_id)

    if data["review"].get("ocrConsultedDuringAuthoring") is not False:
        fail(f"{path}: ground truth must declare OCR was not consulted")


def main() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("annotation schema must declare JSON Schema Draft 2020-12")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("corpusId") != "mangasensei-public-demo-v1"
    ):
        fail("invalid corpus manifest header")
    if manifest["license"]["spdx"] != "CC-BY-4.0":
        fail("corpus license must be CC-BY-4.0")
    expected_inventory = {
        path.relative_to(CORPUS).as_posix()
        for path in CORPUS.rglob("*")
        if path.is_file() and path != MANIFEST_PATH
    }
    manifest_inventory = {entry["file"] for entry in manifest["inventory"]}
    if expected_inventory != manifest_inventory:
        missing = sorted(expected_inventory - manifest_inventory)
        extra = sorted(manifest_inventory - expected_inventory)
        fail(f"manifest inventory mismatch: missing={missing} extra={extra}")
    for entry in manifest["inventory"]:
        file = CORPUS / entry["file"]
        if file.stat().st_size != entry["bytes"] or digest(file) != entry["sha256"]:
            fail(f"inventory integrity mismatch: {entry['file']}")

    page_ids: set[str] = set()
    for page in manifest["pages"]:
        page_id = page["id"]
        if page_id in page_ids:
            fail(f"duplicate page id {page_id}")
        page_ids.add(page_id)
        source = CORPUS / page["source"]["file"]
        image = CORPUS / page["image"]["file"]
        annotation = CORPUS / page["annotation"]["file"]
        integrity_targets = (
            ("source", source, page["source"]["sha256"]),
            ("image", image, page["image"]["sha256"]),
            ("annotation", annotation, page["annotation"]["sha256"]),
        )
        for label, file, expected in integrity_targets:
            if not HEX64.fullmatch(expected) or digest(file) != expected:
                fail(f"{page_id}: {label} SHA-256 mismatch")
        with Image.open(image) as rendered:
            if (
                rendered.format != "PNG"
                or rendered.mode != "RGB"
                or rendered.size != (1440, 2048)
                or getattr(rendered, "n_frames", 1) != 1
            ):
                fail(f"{page_id}: image contract mismatch")
        source_text = source.read_text(encoding="utf-8")
        if f'data-page-id="{page_id}"' not in source_text:
            fail(f"{page_id}: source page marker missing")
        validate_annotation(annotation, source_text, schema)
        annotation_data = json.loads(annotation.read_text(encoding="utf-8"))
        if annotation_data["page"]["id"] != page_id:
            fail(f"{page_id}: annotation page ID mismatch")
        if annotation_data["page"]["split"] != page["split"]:
            fail(f"{page_id}: annotation split mismatch")
        if annotation_data["page"]["imageSha256"] != digest(image):
            fail(f"{page_id}: annotation image hash does not match PNG")

    if len(page_ids) != 4:
        fail(f"public-demo v1 must contain exactly four pages, got {len(page_ids)}")

    fonts = json.loads((CORPUS / "provenance" / "fonts.json").read_text(encoding="utf-8"))
    if fonts["license"] != "OFL-1.1" or fonts["redistributedInRepository"] is not False:
        fail("font provenance contract mismatch")
    for font in fonts["fonts"]:
        if not HEX64.fullmatch(font["sha256"]) or re.fullmatch(
            r"[0-9a-f]{40}", font["gitBlobSha1"]
        ) is None:
            fail(f"font integrity metadata incomplete: {font['id']}")

    toolchain = json.loads(
        (CORPUS / "provenance" / "toolchain.json").read_text(encoding="utf-8")
    )
    if (
        toolchain["playwrightVersion"] != "1.62.1"
        or toolchain["renderedChromiumVersion"].startswith("__")
    ):
        fail("render toolchain is not frozen")
    print(
        "public-demo corpus contract valid: "
        f"{len(page_ids)} pages, {len(manifest_inventory)} inventory files"
    )


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"public-demo validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
