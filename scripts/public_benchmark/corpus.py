from __future__ import annotations

import unicodedata
from pathlib import Path

from .contracts import (
    _HEX64,
    CorpusBundle,
    GroundTruthPage,
    GroundTruthRegion,
    JsonObject,
    NegativeZone,
    _array,
    _bbox,
    _boolean,
    _corpus_file,
    _error,
    _exact_keys,
    _hex,
    _integer,
    _manifest_inventory_digest,
    _object,
    _png_dimensions,
    _polygon,
    _read_json_bytes,
    _string,
    sha256_bytes,
    sha256_path,
)


def _parse_gt_region(value: object, location: str, width: int, height: int) -> GroundTruthRegion:
    region = _object(value, location)
    required = {
        "id",
        "geometry",
        "transcription",
        "orientation",
        "textRole",
        "textForm",
        "readingOrder",
        "difficultCaseTags",
        "scoring",
    }
    _exact_keys(region, required, location)
    region_id = _string(region["id"], f"{location}.id")
    geometry = _object(region["geometry"], f"{location}.geometry")
    _exact_keys(geometry, {"bbox", "polygon"}, f"{location}.geometry")
    bbox = _bbox(geometry["bbox"], f"{location}.geometry.bbox", width, height)
    polygon = _polygon(
        geometry["polygon"],
        f"{location}.geometry.polygon",
        width,
        height,
        nullable=False,
        min_points=4,
    )
    assert polygon is not None

    transcription = _object(region["transcription"], f"{location}.transcription")
    _exact_keys(transcription, {"raw", "normalization"}, f"{location}.transcription")
    raw = _string(transcription["raw"], f"{location}.transcription.raw")
    normalization = _string(
        transcription["normalization"], f"{location}.transcription.normalization"
    )
    if normalization != "strict-nfc-v1":
        raise _error(f"{location}.transcription.normalization", "expected strict-nfc-v1")
    if not unicodedata.is_normalized("NFC", raw):
        raise _error(f"{location}.transcription.raw", "ground truth must already be NFC")

    orientation = _string(region["orientation"], f"{location}.orientation")
    if orientation not in {"vertical-rl", "horizontal-ltr", "angled", "mixed"}:
        raise _error(f"{location}.orientation", "unsupported orientation")
    text_role = _string(region["textRole"], f"{location}.textRole")
    if text_role not in {"dialogue", "thought", "narration", "environmental", "sfx", "uncertain"}:
        raise _error(f"{location}.textRole", "unsupported text role")
    text_form = _string(region["textForm"], f"{location}.textForm")
    if text_form not in {"base", "ruby"}:
        raise _error(f"{location}.textForm", "expected base or ruby")

    order = _object(region["readingOrder"], f"{location}.readingOrder")
    _exact_keys(order, {"position", "scored"}, f"{location}.readingOrder")
    order_scored = _boolean(order["scored"], f"{location}.readingOrder.scored")
    position_value = order["position"]
    if position_value is None:
        order_position = None
    else:
        order_position = _integer(position_value, f"{location}.readingOrder.position", minimum=0)

    scoring = _object(region["scoring"], f"{location}.scoring")
    _exact_keys(scoring, {"detection", "recognition", "readingOrder"}, f"{location}.scoring")
    detection_scored = _boolean(scoring["detection"], f"{location}.scoring.detection")
    recognition_scored = _boolean(scoring["recognition"], f"{location}.scoring.recognition")
    reading_order_scored = _boolean(scoring["readingOrder"], f"{location}.scoring.readingOrder")
    if order_scored != reading_order_scored:
        raise _error(location, "reading-order scoring flags disagree")
    if reading_order_scored != (order_position is not None):
        raise _error(location, "reading-order position must be present exactly when scored")

    return GroundTruthRegion(
        id=region_id,
        bbox=bbox,
        polygon=polygon,
        transcription_raw=raw,
        text_role=text_role,
        text_form=text_form,
        detection_scored=detection_scored,
        recognition_scored=recognition_scored,
        reading_order_scored=reading_order_scored,
        reading_order_position=order_position,
    )


def _parse_annotation(
    data: object,
    *,
    location: str,
    expected_page_id: str,
    expected_image_sha256: str,
    expected_width: int,
    expected_height: int,
) -> tuple[
    tuple[GroundTruthRegion, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[NegativeZone, ...],
    tuple[str, ...],
]:
    annotation = _object(data, location)
    _exact_keys(
        annotation,
        {
            "schemaVersion",
            "page",
            "regions",
            "furiganaRelations",
            "presentationMarks",
            "negativeZones",
            "readingOrderContract",
            "review",
        },
        location,
    )
    if _string(annotation["schemaVersion"], f"{location}.schemaVersion") != "1.0.0":
        raise _error(f"{location}.schemaVersion", "unsupported annotation schema version")

    page = _object(annotation["page"], f"{location}.page")
    _exact_keys(
        page,
        {"id", "imageSha256", "width", "height", "split", "license", "provenanceRef"},
        f"{location}.page",
    )
    if _string(page.get("id"), f"{location}.page.id") != expected_page_id:
        raise _error(f"{location}.page.id", "does not match manifest page id")
    if (
        _hex(page.get("imageSha256"), f"{location}.page.imageSha256", _HEX64)
        != expected_image_sha256
    ):
        raise _error(f"{location}.page.imageSha256", "does not match manifest image hash")
    if _integer(page.get("width"), f"{location}.page.width", minimum=1) != expected_width:
        raise _error(f"{location}.page.width", "does not match manifest width")
    if _integer(page.get("height"), f"{location}.page.height", minimum=1) != expected_height:
        raise _error(f"{location}.page.height", "does not match manifest height")
    if _string(page.get("license"), f"{location}.page.license") != "CC-BY-4.0":
        raise _error(f"{location}.page.license", "expected CC-BY-4.0")
    _string(page.get("split"), f"{location}.page.split")
    _string(page.get("provenanceRef"), f"{location}.page.provenanceRef")

    regions_raw = _array(annotation["regions"], f"{location}.regions")
    regions = tuple(
        _parse_gt_region(item, f"{location}.regions[{index}]", expected_width, expected_height)
        for index, item in enumerate(regions_raw)
    )
    region_ids = [region.id for region in regions]
    if len(region_ids) != len(set(region_ids)):
        raise _error(f"{location}.regions", "duplicate region id")
    by_id = {region.id: region for region in regions}

    scored_order = sorted(
        (region for region in regions if region.reading_order_scored),
        key=lambda region: (
            region.reading_order_position if region.reading_order_position is not None else -1
        ),
    )
    expected_positions = list(range(len(scored_order)))
    actual_positions = [region.reading_order_position for region in scored_order]
    if actual_positions != expected_positions:
        raise _error(
            f"{location}.regions", "scored reading-order positions must be contiguous from zero"
        )

    furigana_ids: list[str] = []
    furigana_values = _array(
        annotation["furiganaRelations"], f"{location}.furiganaRelations"
    )
    for index, relation_value in enumerate(furigana_values):
        relation_location = f"{location}.furiganaRelations[{index}]"
        relation = _object(relation_value, relation_location)
        _exact_keys(
            relation,
            {"id", "rubyRegionId", "baseRegionId", "baseTextSpan", "reading"},
            relation_location,
        )
        relation_id = _string(relation["id"], f"{relation_location}.id")
        ruby_id = _string(relation["rubyRegionId"], f"{relation_location}.rubyRegionId")
        base_id = _string(relation["baseRegionId"], f"{relation_location}.baseRegionId")
        if ruby_id not in by_id or by_id[ruby_id].text_form != "ruby":
            raise _error(relation_location, "invalid ruby region")
        if base_id not in by_id or by_id[base_id].text_form != "base":
            raise _error(relation_location, "invalid base region")
        span = _object(relation["baseTextSpan"], f"{relation_location}.baseTextSpan")
        _exact_keys(span, {"start", "end"}, f"{relation_location}.baseTextSpan")
        start = _integer(span["start"], f"{relation_location}.baseTextSpan.start", minimum=0)
        end = _integer(span["end"], f"{relation_location}.baseTextSpan.end", minimum=1)
        if not start < end <= len(by_id[base_id].transcription_raw):
            raise _error(f"{relation_location}.baseTextSpan", "span is outside base transcription")
        reading = _string(relation["reading"], f"{relation_location}.reading")
        if reading != by_id[ruby_id].transcription_raw:
            raise _error(f"{relation_location}.reading", "does not match ruby transcription")
        furigana_ids.append(relation_id)
    if len(furigana_ids) != len(set(furigana_ids)):
        raise _error(f"{location}.furiganaRelations", "duplicate relation id")

    presentation_ids: list[str] = []
    presentation_values = _array(
        annotation["presentationMarks"], f"{location}.presentationMarks"
    )
    for index, mark_value in enumerate(presentation_values):
        mark_location = f"{location}.presentationMarks[{index}]"
        mark = _object(mark_value, mark_location)
        _exact_keys(
            mark,
            {
                "id",
                "kind",
                "associatedRegionId",
                "baseTextSpan",
                "geometry",
                "transcriptionEffect",
                "challengeTags",
            },
            mark_location,
        )
        mark_id = _string(mark["id"], f"{mark_location}.id")
        if _string(mark["kind"], f"{mark_location}.kind") != "boten":
            raise _error(f"{mark_location}.kind", "expected boten")
        associated_id = _string(mark["associatedRegionId"], f"{mark_location}.associatedRegionId")
        if associated_id not in by_id or by_id[associated_id].text_form != "base":
            raise _error(mark_location, "presentation mark must reference a base region")
        if _string(mark["transcriptionEffect"], f"{mark_location}.transcriptionEffect") != "none":
            raise _error(
                f"{mark_location}.transcriptionEffect",
                "presentation mark must not change transcription",
            )
        geometry = _object(mark["geometry"], f"{mark_location}.geometry")
        _exact_keys(geometry, {"bbox", "polygon"}, f"{mark_location}.geometry")
        _bbox(geometry["bbox"], f"{mark_location}.geometry.bbox", expected_width, expected_height)
        _polygon(
            geometry["polygon"],
            f"{mark_location}.geometry.polygon",
            expected_width,
            expected_height,
            nullable=False,
            min_points=4,
        )
        presentation_ids.append(mark_id)
    if len(presentation_ids) != len(set(presentation_ids)):
        raise _error(f"{location}.presentationMarks", "duplicate presentation mark id")

    negative_zones: list[NegativeZone] = []
    negative_values = _array(annotation["negativeZones"], f"{location}.negativeZones")
    for index, zone_value in enumerate(negative_values):
        zone_location = f"{location}.negativeZones[{index}]"
        zone = _object(zone_value, zone_location)
        _exact_keys(zone, {"id", "kind", "geometry", "expected", "challengeTags"}, zone_location)
        zone_id = _string(zone["id"], f"{zone_location}.id")
        kind = _string(zone["kind"], f"{zone_location}.kind")
        if _string(zone["expected"], f"{zone_location}.expected") != "no-text-region":
            raise _error(f"{zone_location}.expected", "expected no-text-region")
        geometry = _object(zone["geometry"], f"{zone_location}.geometry")
        _exact_keys(geometry, {"bbox", "polygon"}, f"{zone_location}.geometry")
        bbox = _bbox(
            geometry.get("bbox"),
            f"{location}.negativeZones[{index}].geometry.bbox",
            expected_width,
            expected_height,
        )
        polygon = _polygon(
            geometry.get("polygon"),
            f"{location}.negativeZones[{index}].geometry.polygon",
            expected_width,
            expected_height,
            nullable=False,
            min_points=4,
        )
        assert polygon is not None
        negative_zones.append(NegativeZone(id=zone_id, kind=kind, bbox=bbox, polygon=polygon))
    negative_ids = [zone.id for zone in negative_zones]
    if len(negative_ids) != len(set(negative_ids)):
        raise _error(f"{location}.negativeZones", "duplicate negative-zone id")

    order_contract = _object(annotation["readingOrderContract"], f"{location}.readingOrderContract")
    sequence = tuple(
        _string(item, f"{location}.readingOrderContract.sequence[{index}]")
        for index, item in enumerate(
            _array(order_contract.get("sequence"), f"{location}.readingOrderContract.sequence")
        )
    )
    expected_sequence = tuple(region.id for region in scored_order)
    if sequence != expected_sequence:
        raise _error(
            f"{location}.readingOrderContract.sequence", "does not match scored region positions"
        )

    return (
        regions,
        tuple(sorted(furigana_ids)),
        tuple(sorted(presentation_ids)),
        tuple(sorted(negative_zones, key=lambda zone: zone.id)),
        sequence,
    )


def load_corpus(root: Path) -> CorpusBundle:
    corpus_root = root.resolve()
    manifest_path = corpus_root / "manifest.json"
    schema_path = corpus_root / "annotations" / "schema-v1.json"
    manifest_bytes = manifest_path.read_bytes()
    schema_bytes = schema_path.read_bytes()
    manifest = _object(_read_json_bytes(manifest_bytes, manifest_path.as_posix()), "manifest")
    schema = _object(_read_json_bytes(schema_bytes, schema_path.as_posix()), "annotation-schema")

    schema_version = _integer(manifest.get("schemaVersion"), "manifest.schemaVersion", minimum=1)
    corpus_id = _string(manifest.get("corpusId"), "manifest.corpusId")
    if (
        _string(schema.get("$schema"), "annotation-schema.$schema")
        != "https://json-schema.org/draft/2020-12/schema"
    ):
        raise _error("annotation-schema.$schema", "expected JSON Schema Draft 2020-12")
    annotation_schema_sha256 = sha256_bytes(schema_bytes)
    inventory_schema_sha = _manifest_inventory_digest(manifest, "annotations/schema-v1.json")
    if annotation_schema_sha256 != inventory_schema_sha:
        raise _error("annotation-schema", "SHA-256 does not match manifest inventory")

    pages: list[GroundTruthPage] = []
    seen_page_ids: set[str] = set()
    page_entries = _array(manifest.get("pages"), "manifest.pages")
    if not page_entries:
        raise _error("manifest.pages", "must not be empty")
    for index, page_value in enumerate(page_entries):
        location = f"manifest.pages[{index}]"
        page = _object(page_value, location)
        page_id = _string(page.get("id"), f"{location}.id")
        if page_id in seen_page_ids:
            raise _error(f"{location}.id", "duplicate page id")
        seen_page_ids.add(page_id)

        image = _object(page.get("image"), f"{location}.image")
        annotation = _object(page.get("annotation"), f"{location}.annotation")
        image_file = _string(image.get("file"), f"{location}.image.file")
        annotation_file = _string(annotation.get("file"), f"{location}.annotation.file")
        annotation_schema_file = _string(annotation.get("schema"), f"{location}.annotation.schema")
        if annotation_schema_file != "annotations/schema-v1.json":
            raise _error(f"{location}.annotation.schema", "unsupported annotation schema path")

        image_sha = _hex(image.get("sha256"), f"{location}.image.sha256", _HEX64)
        annotation_sha = _hex(annotation.get("sha256"), f"{location}.annotation.sha256", _HEX64)
        width = _integer(image.get("width"), f"{location}.image.width", minimum=1)
        height = _integer(image.get("height"), f"{location}.image.height", minimum=1)

        image_path = _corpus_file(corpus_root, image_file, f"{location}.image.file")
        annotation_path = _corpus_file(
            corpus_root, annotation_file, f"{location}.annotation.file"
        )
        if sha256_path(image_path) != image_sha:
            raise _error(page_id, "image SHA-256 mismatch")
        if _png_dimensions(image_path) != (width, height):
            raise _error(page_id, "image dimensions do not match manifest")
        annotation_bytes = annotation_path.read_bytes()
        if sha256_bytes(annotation_bytes) != annotation_sha:
            raise _error(page_id, "annotation SHA-256 mismatch")
        if _manifest_inventory_digest(manifest, image_file) != image_sha:
            raise _error(page_id, "image hash disagrees with manifest inventory")
        if _manifest_inventory_digest(manifest, annotation_file) != annotation_sha:
            raise _error(page_id, "annotation hash disagrees with manifest inventory")

        annotation_data = _read_json_bytes(annotation_bytes, annotation_path.as_posix())
        regions, furigana_ids, presentation_ids, negative_zones, order_sequence = _parse_annotation(
            annotation_data,
            location=annotation_path.as_posix(),
            expected_page_id=page_id,
            expected_image_sha256=image_sha,
            expected_width=width,
            expected_height=height,
        )
        pages.append(
            GroundTruthPage(
                id=page_id,
                width=width,
                height=height,
                image_sha256=image_sha,
                annotation_sha256=annotation_sha,
                regions=regions,
                furigana_relation_ids=furigana_ids,
                presentation_mark_ids=presentation_ids,
                negative_zones=negative_zones,
                reading_order_sequence=order_sequence,
            )
        )

    return CorpusBundle(
        root=corpus_root,
        corpus_id=corpus_id,
        schema_version=schema_version,
        manifest_sha256=sha256_bytes(manifest_bytes),
        annotation_schema_sha256=annotation_schema_sha256,
        pages=tuple(pages),
    )
