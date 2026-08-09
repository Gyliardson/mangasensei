"""In-process adapter for the reviewed manga-image-translator subset."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from PIL import Image

from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.ocr.contracts import OcrImage, OcrProvenance, OcrRegionResult, OcrResult
from mangasensei.ocr.models.manifest import ModelManifest, verify_model

from .recognizer_48px import RECOGNITION_SHORT_AXIS_PADDING

DETECTOR_NAME = "default"
RECOGNIZER_NAME = "48px"
UPSTREAM_REPOSITORY = "https://github.com/zyddnys/manga-image-translator"
_CONFIG_SCHEMA_VERSION = "manga-image-translator-v4"
_READING_ORDER_VERSION = "manga-tiers-v1"
_RECOGNITION_WARP_VERSION = "full-image-context-v1"
_DETECTOR_FLAGS = (False, False, False, False, False)
_RECOGNIZER_FLAG = False
_UPSTREAM_RECOGNIZER_LOGGER = "manga-translator.Model48pxOCR"
_MIN_TIER_BAND_PAGE_FRACTION = 0.02
_MAX_TIER_BAND_PAGE_FRACTION = 0.12
_TIER_BAND_REGION_HEIGHT_FRACTION = 0.5


@dataclass(frozen=True, slots=True)
class _OcrConfig:
    prob: float | None = None
    ignore_bubble: int = 0


@dataclass(frozen=True, slots=True)
class _ReadingOrderItem:
    source_index: int
    region: Any
    x_center: float
    y_top: float
    height: float


def region_from_upstream(
    region: Any,
    *,
    image_sha256: str,
    dimensions: PageDimensions,
    reading_order: int,
    upstream_commit: str,
) -> OcrRegionResult:
    x1, y1, x2, y2 = (int(value) for value in region.xyxy)
    x1 = max(0, min(x1, dimensions.width - 1))
    y1 = max(0, min(y1, dimensions.height - 1))
    x2 = max(x1 + 1, min(x2, dimensions.width))
    y2 = max(y1 + 1, min(y2, dimensions.height))
    bbox = BoundingBox(x=x1, y=y1, width=x2 - x1, height=y2 - y1)
    polygon_source = region.min_rect[0] if getattr(region, "min_rect", None) is not None else None
    polygon = (
        tuple(
            (
                max(0, min(int(point[0]), dimensions.width)),
                max(0, min(int(point[1]), dimensions.height)),
            )
            for point in polygon_source
        )
        if polygon_source is not None
        else None
    )
    stable_input = ":".join(
        (
            image_sha256,
            str(reading_order),
            str(bbox.x),
            str(bbox.y),
            str(bbox.width),
            str(bbox.height),
        )
    )
    confidence = max(0.0, min(float(region.prob), 1.0))
    return OcrRegionResult(
        id=str(uuid5(NAMESPACE_URL, f"mangasensei:ocr:{stable_input}")),
        dimensions=dimensions,
        bbox=bbox,
        normalized_bbox=bbox.normalize(dimensions),
        polygon=polygon,
        angle=max(-180.0, min(float(region.angle), 180.0)),
        confidence=confidence,
        japanese_text=str(region.text).strip(),
        reading_order=reading_order,
        detector=DETECTOR_NAME,
        recognizer=RECOGNIZER_NAME,
        upstream_commit=upstream_commit,
    )


class MangaImageTranslatorEngine:
    """Warm in-process detector and OCR engine used only by the worker process."""

    def __init__(
        self,
        *,
        model_cache: Path,
        device: str = "cpu",
        detection_size: int = 2048,
        text_threshold: float = 0.5,
        box_threshold: float = 0.7,
        unclip_ratio: float = 2.3,
        minimum_confidence: float = 0.2,
    ) -> None:
        _configure_upstream_logging()
        self._model_cache = model_cache.resolve()
        self._device = device
        self._detection_size = detection_size
        self._text_threshold = text_threshold
        self._box_threshold = box_threshold
        self._unclip_ratio = unclip_ratio
        self._ocr_config = _OcrConfig(prob=minimum_confidence)
        self._detector: Any | None = None
        self._recognizer: Any | None = None
        self._lock = asyncio.Lock()

    async def analyze(self, image: OcrImage) -> OcrResult:
        async with self._lock:
            detector, recognizer, merge, manifest = await self._ensure_loaded()
            provenance = self.provenance_for_manifest(manifest)
            pixels = _decode_rgb(image.content)
            textlines, _, _ = await detector.detect(
                pixels,
                self._detection_size,
                self._text_threshold,
                self._box_threshold,
                self._unclip_ratio,
                *_DETECTOR_FLAGS,
            )
            recognized = await recognizer.recognize(
                pixels, textlines, self._ocr_config, _RECOGNIZER_FLAG
            )
            recognized = [line for line in recognized if str(line.text).strip()]
            merged = await merge(recognized, image.dimensions.width, image.dimensions.height)
            ordered = _manga_reading_order(merged, page_height=image.dimensions.height)
            regions = tuple(
                region_from_upstream(
                    region,
                    image_sha256=image.sha256,
                    dimensions=image.dimensions,
                    reading_order=index,
                    upstream_commit=manifest.upstream_commit,
                )
                for index, region in enumerate(ordered[:128])
                if str(region.text).strip()
            )
            return OcrResult(
                image_sha256=image.sha256,
                provenance=provenance,
                regions=regions,
            )

    def provenance_for_manifest(self, manifest: ModelManifest) -> OcrProvenance:
        """Describe the reviewed manifest and effective output-affecting OCR configuration."""
        config = {
            "schema_version": _CONFIG_SCHEMA_VERSION,
            "device": self._device,
            "detection_size": self._detection_size,
            "text_threshold": self._text_threshold,
            "box_threshold": self._box_threshold,
            "unclip_ratio": self._unclip_ratio,
            "minimum_confidence": self._ocr_config.prob,
            "ignore_bubble": self._ocr_config.ignore_bubble,
            "detector_flags": _DETECTOR_FLAGS,
            "recognizer_flag": _RECOGNIZER_FLAG,
            "recognition_warp": _RECOGNITION_WARP_VERSION,
            "recognition_short_axis_padding": RECOGNITION_SHORT_AXIS_PADDING,
            "reading_order": _READING_ORDER_VERSION,
        }
        canonical_config = json.dumps(config, sort_keys=True, separators=(",", ":"))
        return OcrProvenance(
            detector=DETECTOR_NAME,
            recognizer=RECOGNIZER_NAME,
            model_manifest_version=manifest.version,
            config_digest=hashlib.sha256(canonical_config.encode()).digest(),
            upstream_repository=UPSTREAM_REPOSITORY,
            upstream_commit=manifest.upstream_commit,
        )

    async def _ensure_loaded(self) -> tuple[Any, Any, Any, ModelManifest]:
        from ..vendor.manga_image_translator.manga_translator.detection.default import (
            DefaultDetector,
        )
        from ..vendor.manga_image_translator.manga_translator.textline_merge import (
            dispatch,
        )
        from .recognizer_48px import MangaSenseiModel48pxOCR

        manifest = self._verify_required_models()
        if self._detector is None:
            DefaultDetector._MODEL_DIR = str(self._model_cache)
            self._detector = DefaultDetector()  # type: ignore[no-untyped-call]
            await self._detector.load(self._device)
        if self._recognizer is None:
            MangaSenseiModel48pxOCR._MODEL_DIR = str(self._model_cache)
            self._recognizer = MangaSenseiModel48pxOCR()
            await self._recognizer.load(self._device)
        return self._detector, self._recognizer, dispatch, manifest

    def _verify_required_models(self) -> ModelManifest:
        manifest_path = Path(__file__).parents[1] / "models" / "manifest.json"
        manifest = ModelManifest.load(manifest_path)
        for filename, subdirectory in (
            ("detect-20241225.ckpt", "detection"),
            ("ocr_ar_48px.ckpt", "ocr"),
            ("alphabet-all-v7.txt", "ocr"),
        ):
            verify_model(self._model_cache / subdirectory / filename, manifest.artifact(filename))
        return manifest


def _configure_upstream_logging() -> None:
    """Keep recognized manga text out of normal worker logs."""
    recognizer_logger = logging.getLogger(_UPSTREAM_RECOGNIZER_LOGGER)
    if recognizer_logger.level == logging.NOTSET or recognizer_logger.level < logging.WARNING:
        recognizer_logger.setLevel(logging.WARNING)


def _decode_rgb(content: bytes) -> Any:
    import numpy as np

    with Image.open(io.BytesIO(content)) as image:
        return np.asarray(image.convert("RGB"))


def _manga_reading_order(regions: list[Any], *, page_height: int) -> list[Any]:
    """Order text by top-to-bottom page tiers and right-to-left position within each tier."""
    if len(regions) < 2:
        return list(regions)

    items: list[_ReadingOrderItem] = []
    for source_index, region in enumerate(regions):
        x1, y1, x2, y2 = (float(value) for value in region.xyxy)
        items.append(
            _ReadingOrderItem(
                source_index=source_index,
                region=region,
                x_center=(x1 + x2) / 2,
                y_top=y1,
                height=max(1.0, y2 - y1),
            )
        )

    median_height = median(item.height for item in items)
    tier_band = max(
        page_height * _MIN_TIER_BAND_PAGE_FRACTION,
        min(
            page_height * _MAX_TIER_BAND_PAGE_FRACTION,
            median_height * _TIER_BAND_REGION_HEIGHT_FRACTION,
        ),
    )
    by_top = sorted(items, key=lambda item: (item.y_top, -item.x_center, item.source_index))

    tiers: list[tuple[float, list[_ReadingOrderItem]]] = []
    for item in by_top:
        if not tiers or item.y_top - tiers[-1][0] > tier_band:
            tiers.append((item.y_top, [item]))
        else:
            tiers[-1][1].append(item)

    ordered: list[Any] = []
    for _, tier_items in tiers:
        tier_items.sort(key=lambda item: (-item.x_center, item.y_top, item.source_index))
        ordered.extend(item.region for item in tier_items)
    return ordered
