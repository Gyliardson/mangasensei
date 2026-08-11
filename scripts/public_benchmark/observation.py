from __future__ import annotations

from pathlib import Path

from .contracts import (
    OBSERVATION_KIND,
    OBSERVATION_SCHEMA_VERSION,
    _HEX40,
    _HEX64,
    Observation,
    ObservationPage,
    ObservedRegion,
    _allowed_keys,
    _array,
    _bbox,
    _boolean,
    _error,
    _exact_keys,
    _hex,
    _integer,
    _number,
    _object,
    _polygon,
    _read_json_bytes,
    _string,
    sha256_bytes,
)


def _parse_observed_region(value: object, location: str, width: int, height: int) -> ObservedRegion:
    region = _object(value, location)
    _exact_keys(
        region,
        {"id", "bbox", "polygon", "angle", "confidence", "text", "readingOrder"},
        location,
    )
    region_id = _string(region["id"], f"{location}.id")
    bbox = _bbox(region["bbox"], f"{location}.bbox", width, height)
    polygon = _polygon(region["polygon"], f"{location}.polygon", width, height, nullable=True)
    angle = _number(region["angle"], f"{location}.angle")
    if not -180 <= angle <= 180:
        raise _error(f"{location}.angle", "must be between -180 and 180")
    confidence = _number(region["confidence"], f"{location}.confidence")
    if not 0 <= confidence <= 1:
        raise _error(f"{location}.confidence", "must be between 0 and 1")
    text = _string(region["text"], f"{location}.text")
    reading_order = _integer(region["readingOrder"], f"{location}.readingOrder", minimum=0)
    return ObservedRegion(
        id=region_id,
        bbox=bbox,
        polygon=polygon,
        angle=angle,
        confidence=confidence,
        text=text,
        reading_order=reading_order,
    )


def load_observation(path: Path) -> Observation:
    observation_path = path.resolve()
    raw = observation_path.read_bytes()
    data = _object(_read_json_bytes(raw, observation_path.as_posix()), "observation")
    _exact_keys(
        data,
        {"schemaVersion", "kind", "corpus", "producer", "features", "ocr", "runtime", "pages"},
        "observation",
    )
    schema_version = _string(data["schemaVersion"], "observation.schemaVersion")
    if schema_version != OBSERVATION_SCHEMA_VERSION:
        raise _error("observation.schemaVersion", "unsupported observation schema version")
    kind = _string(data["kind"], "observation.kind")
    if kind != OBSERVATION_KIND:
        raise _error("observation.kind", f"expected {OBSERVATION_KIND}")

    corpus = _object(data["corpus"], "observation.corpus")
    _exact_keys(
        corpus,
        {"id", "schemaVersion", "manifestSha256", "annotationSchemaSha256"},
        "observation.corpus",
    )
    corpus_id = _string(corpus["id"], "observation.corpus.id")
    corpus_schema_version = _integer(
        corpus["schemaVersion"], "observation.corpus.schemaVersion", minimum=1
    )
    manifest_sha = _hex(corpus["manifestSha256"], "observation.corpus.manifestSha256", _HEX64)
    annotation_schema_sha = _hex(
        corpus["annotationSchemaSha256"], "observation.corpus.annotationSchemaSha256", _HEX64
    )

    producer = _object(data["producer"], "observation.producer")
    _exact_keys(
        producer,
        {"repositorySha", "mangaSenseiVersion", "sourceOcrContract", "capturedAt"},
        "observation.producer",
    )
    _hex(producer["repositorySha"], "observation.producer.repositorySha", _HEX40)
    _string(producer["mangaSenseiVersion"], "observation.producer.mangaSenseiVersion")
    _string(producer["sourceOcrContract"], "observation.producer.sourceOcrContract")
    _string(producer["capturedAt"], "observation.producer.capturedAt")

    features = _object(data["features"], "observation.features")
    expected_features = {
        "rawPolygon": True,
        "angle": True,
        "confidence": True,
        "readingOrder": True,
        "presentationMarks": False,
        "furiganaRelationships": False,
        "textRole": False,
        "linguistics": False,
    }
    _exact_keys(features, set(expected_features), "observation.features")
    for name, expected in expected_features.items():
        actual = _boolean(features[name], f"observation.features.{name}")
        if actual is not expected:
            raise _error(f"observation.features.{name}", f"v1 requires {expected}")

    ocr = _object(data["ocr"], "observation.ocr")
    _exact_keys(
        ocr,
        {
            "detector",
            "recognizer",
            "modelManifestVersion",
            "modelManifestSha256",
            "configDigestSha256",
            "upstreamRepository",
            "upstreamCommit",
            "modelArtifacts",
        },
        "observation.ocr",
    )
    _string(ocr["detector"], "observation.ocr.detector")
    _string(ocr["recognizer"], "observation.ocr.recognizer")
    _string(ocr["modelManifestVersion"], "observation.ocr.modelManifestVersion")
    _hex(ocr["modelManifestSha256"], "observation.ocr.modelManifestSha256", _HEX64)
    _hex(ocr["configDigestSha256"], "observation.ocr.configDigestSha256", _HEX64)
    _string(ocr["upstreamRepository"], "observation.ocr.upstreamRepository")
    _hex(ocr["upstreamCommit"], "observation.ocr.upstreamCommit", _HEX40)
    artifact_names: set[str] = set()
    artifact_values = _array(ocr["modelArtifacts"], "observation.ocr.modelArtifacts")
    for index, artifact_value in enumerate(artifact_values):
        artifact = _object(artifact_value, f"observation.ocr.modelArtifacts[{index}]")
        _allowed_keys(
            artifact,
            {"filename", "sha256"},
            {"sizeBytes"},
            f"observation.ocr.modelArtifacts[{index}]",
        )
        filename = _string(
            artifact["filename"], f"observation.ocr.modelArtifacts[{index}].filename"
        )
        if Path(filename).name != filename:
            raise _error(f"observation.ocr.modelArtifacts[{index}].filename", "must be a basename")
        if filename in artifact_names:
            raise _error("observation.ocr.modelArtifacts", "duplicate filename")
        artifact_names.add(filename)
        _hex(artifact["sha256"], f"observation.ocr.modelArtifacts[{index}].sha256", _HEX64)
        if "sizeBytes" in artifact:
            _integer(
                artifact["sizeBytes"],
                f"observation.ocr.modelArtifacts[{index}].sizeBytes",
                minimum=1,
            )

    runtime = _object(data["runtime"], "observation.runtime")
    runtime_allowed = {"python", "opencv", "numpy", "torch", "device", "platform", "architecture"}
    if set(runtime) - runtime_allowed:
        raise _error(
            "observation.runtime",
            f"unexpected properties {sorted(set(runtime) - runtime_allowed)}",
        )
    for name, value in runtime.items():
        _string(value, f"observation.runtime.{name}")

    pages: list[ObservationPage] = []
    page_ids: set[str] = set()
    global_region_ids: set[str] = set()
    page_values = _array(data["pages"], "observation.pages")
    if not page_values:
        raise _error("observation.pages", "must not be empty")
    for index, page_value in enumerate(page_values):
        location = f"observation.pages[{index}]"
        page = _object(page_value, location)
        _exact_keys(
            page,
            {"id", "imageSha256", "annotationSha256", "width", "height", "regions"},
            location,
        )
        page_id = _string(page["id"], f"{location}.id")
        if page_id in page_ids:
            raise _error(f"{location}.id", "duplicate page id")
        page_ids.add(page_id)
        width = _integer(page["width"], f"{location}.width", minimum=1)
        height = _integer(page["height"], f"{location}.height", minimum=1)
        image_sha = _hex(page["imageSha256"], f"{location}.imageSha256", _HEX64)
        annotation_sha = _hex(page["annotationSha256"], f"{location}.annotationSha256", _HEX64)
        regions = tuple(
            _parse_observed_region(item, f"{location}.regions[{region_index}]", width, height)
            for region_index, item in enumerate(_array(page["regions"], f"{location}.regions"))
        )
        if len(regions) > 128:
            raise _error(f"{location}.regions", "exceeds current OcrResult region bound of 128")
        orders = [region.reading_order for region in regions]
        if sorted(orders) != list(range(len(regions))):
            raise _error(
                f"{location}.regions", "readingOrder must be unique and contiguous from zero"
            )
        for region in regions:
            if region.id in global_region_ids:
                raise _error(f"{location}.regions", f"duplicate observation region id {region.id}")
            global_region_ids.add(region.id)
        pages.append(
            ObservationPage(
                id=page_id,
                image_sha256=image_sha,
                annotation_sha256=annotation_sha,
                width=width,
                height=height,
                regions=regions,
            )
        )

    return Observation(
        path=observation_path,
        sha256=sha256_bytes(raw),
        schema_version=schema_version,
        kind=kind,
        corpus_id=corpus_id,
        corpus_schema_version=corpus_schema_version,
        manifest_sha256=manifest_sha,
        annotation_schema_sha256=annotation_schema_sha,
        producer=producer,
        features=features,
        ocr=ocr,
        runtime=runtime,
        pages=tuple(pages),
    )

