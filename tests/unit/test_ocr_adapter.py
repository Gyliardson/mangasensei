from __future__ import annotations

from pathlib import Path

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    UPSTREAM_REPOSITORY,
    MangaImageTranslatorEngine,
    region_from_upstream,
)
from mangasensei.ocr.models.manifest import ModelManifest


class UpstreamRegion:
    xyxy = (100, 200, 400, 700)
    min_rect = (((100, 200), (400, 200), (400, 700), (100, 700)),)
    angle = 0.0
    prob = 0.875
    text = "猫です"


def test_upstream_region_is_converted_without_renderer_fields() -> None:
    dimensions = PageDimensions(width=1000, height=2000)
    upstream_commit = "b" * 40

    first = region_from_upstream(
        UpstreamRegion(),
        image_sha256="a" * 64,
        dimensions=dimensions,
        reading_order=0,
        upstream_commit=upstream_commit,
    )
    second = region_from_upstream(
        UpstreamRegion(),
        image_sha256="a" * 64,
        dimensions=dimensions,
        reading_order=0,
        upstream_commit=upstream_commit,
    )

    assert first == second
    assert first.bbox.model_dump() == {"x": 100, "y": 200, "width": 300, "height": 500}
    assert first.normalized_bbox.model_dump() == {
        "x": 0.1,
        "y": 0.1,
        "width": 0.3,
        "height": 0.25,
    }
    assert first.polygon == ((100, 200), (400, 200), (400, 700), (100, 700))
    assert first.japanese_text == "猫です"
    assert first.detector == "default"
    assert first.recognizer == "48px"
    assert first.upstream_commit == upstream_commit


def test_engine_provenance_uses_supplied_manifest_and_effective_config(tmp_path: Path) -> None:
    manifest = ModelManifest(
        version="fixture-manifest-v2",
        upstream_commit="c" * 40,
        artifacts=(),
    )
    engine = MangaImageTranslatorEngine(model_cache=tmp_path)
    identical = MangaImageTranslatorEngine(model_cache=tmp_path)
    changed = MangaImageTranslatorEngine(model_cache=tmp_path, detection_size=1024)

    provenance = engine.provenance_for_manifest(manifest)
    identical_provenance = identical.provenance_for_manifest(manifest)
    changed_provenance = changed.provenance_for_manifest(manifest)

    assert provenance.detector == "default"
    assert provenance.recognizer == "48px"
    assert provenance.model_manifest_version == "fixture-manifest-v2"
    assert provenance.upstream_repository == UPSTREAM_REPOSITORY
    assert provenance.upstream_commit == "c" * 40
    assert len(provenance.config_digest) == 32
    assert provenance.config_digest == identical_provenance.config_digest
    assert provenance.config_digest != changed_provenance.config_digest
