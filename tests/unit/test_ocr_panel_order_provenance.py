from __future__ import annotations

from pathlib import Path

from mangasensei.ocr.adapter import manga_image_translator as adapter
from mangasensei.ocr.models.manifest import ModelManifest


def test_panel_reading_order_version_is_part_of_ocr_config_digest(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    manifest = ModelManifest(
        version="fixture-manifest",
        upstream_commit="c" * 40,
        artifacts=(),
    )
    engine = adapter.MangaImageTranslatorEngine(model_cache=tmp_path)
    baseline = engine.provenance_for_manifest(manifest)

    assert adapter._READING_ORDER_VERSION == "panel-flow-v1"

    monkeypatch.setattr(adapter, "_READING_ORDER_VERSION", "fixture-panel-flow-v2")  # type: ignore[attr-defined]
    changed = engine.provenance_for_manifest(manifest)

    assert changed.config_digest != baseline.config_digest
    assert changed.detector == baseline.detector
    assert changed.recognizer == baseline.recognizer
    assert changed.model_manifest_version == baseline.model_manifest_version
    assert changed.upstream_commit == baseline.upstream_commit
