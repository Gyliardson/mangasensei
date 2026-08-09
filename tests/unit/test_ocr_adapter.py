from __future__ import annotations

import logging
from pathlib import Path

import pytest

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    UPSTREAM_REPOSITORY,
    MangaImageTranslatorEngine,
    _manga_reading_order,
    region_from_upstream,
)
from mangasensei.ocr.models.manifest import ModelManifest


class UpstreamRegion:
    xyxy = (100, 200, 400, 700)
    min_rect = (((100, 200), (400, 200), (400, 700), (100, 700)),)
    angle = 0.0
    prob = 0.875
    text = "猫です"


class GeometryRegion:
    def __init__(self, name: str, xyxy: tuple[int, int, int, int]) -> None:
        self.name = name
        self.xyxy = xyxy


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


def test_engine_keeps_upstream_recognized_text_out_of_info_logs(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    recognizer_logger = logging.getLogger("manga-translator.Model48pxOCR")
    previous_level = recognizer_logger.level
    try:
        recognizer_logger.setLevel(logging.NOTSET)
        with caplog.at_level(logging.INFO):
            MangaImageTranslatorEngine(model_cache=tmp_path)
            recognizer_logger.info("prob: 0.99 秘密の本文")

        assert recognizer_logger.level >= logging.WARNING
        assert "秘密の本文" not in caplog.text
    finally:
        recognizer_logger.setLevel(previous_level)


def test_manga_reading_order_finishes_upper_tier_before_lower_right_region() -> None:
    upper_right = GeometryRegion("upper-right", (720, 100, 840, 300))
    upper_left = GeometryRegion("upper-left", (300, 150, 420, 330))
    lower_right = GeometryRegion("lower-right", (780, 620, 900, 800))

    ordered = _manga_reading_order(
        [lower_right, upper_left, upper_right],
        page_height=1000,
    )

    assert [region.name for region in ordered] == ["upper-right", "upper-left", "lower-right"]


def test_manga_reading_order_is_right_to_left_within_shared_tier() -> None:
    right = GeometryRegion("right", (700, 100, 820, 300))
    center = GeometryRegion("center", (450, 125, 570, 305))
    left = GeometryRegion("left", (180, 145, 300, 325))

    ordered = _manga_reading_order([left, right, center], page_height=1000)

    assert [region.name for region in ordered] == ["right", "center", "left"]


def test_manga_reading_order_does_not_globally_prioritize_vertical_text() -> None:
    upper_horizontal = GeometryRegion("upper-horizontal", (480, 100, 760, 180))
    upper_vertical = GeometryRegion("upper-vertical", (800, 120, 900, 320))
    lower_vertical = GeometryRegion("lower-vertical", (850, 620, 950, 860))

    ordered = _manga_reading_order(
        [lower_vertical, upper_horizontal, upper_vertical],
        page_height=1000,
    )

    assert [region.name for region in ordered] == [
        "upper-vertical",
        "upper-horizontal",
        "lower-vertical",
    ]


def test_manga_reading_order_is_stable_for_identical_geometry() -> None:
    first = GeometryRegion("first", (600, 100, 700, 300))
    second = GeometryRegion("second", (600, 100, 700, 300))

    ordered = _manga_reading_order([first, second], page_height=1000)

    assert [region.name for region in ordered] == ["first", "second"]
