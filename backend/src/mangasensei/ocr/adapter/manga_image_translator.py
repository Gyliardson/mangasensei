"""In-process adapter for the reviewed manga-image-translator subset."""

from __future__ import annotations

import asyncio
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from PIL import Image

from mangasensei.domain.models import BoundingBox, PageDimensions
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult
from mangasensei.ocr.models.manifest import ModelManifest, verify_model

UPSTREAM_COMMIT = "95227a2bb0fd306cd4f0c104d57284026f991b3a"


@dataclass(frozen=True, slots=True)
class _OcrConfig:
    prob: float | None = None
    ignore_bubble: int = 0


def region_from_upstream(
    region: Any,
    *,
    image_sha256: str,
    dimensions: PageDimensions,
    reading_order: int,
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
        detector="default",
        recognizer="48px",
        upstream_commit=UPSTREAM_COMMIT,
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
            detector, recognizer, merge = await self._ensure_loaded()
            pixels = _decode_rgb(image.content)
            textlines, _, _ = await detector.detect(
                pixels,
                self._detection_size,
                self._text_threshold,
                self._box_threshold,
                self._unclip_ratio,
                False,
                False,
                False,
                False,
                False,
            )
            recognized = await recognizer.recognize(pixels, textlines, self._ocr_config, False)
            recognized = [line for line in recognized if str(line.text).strip()]
            merged = await merge(recognized, image.dimensions.width, image.dimensions.height)
            ordered = _simple_reading_order(merged)
            regions = tuple(
                region_from_upstream(
                    region,
                    image_sha256=image.sha256,
                    dimensions=image.dimensions,
                    reading_order=index,
                )
                for index, region in enumerate(ordered[:128])
                if str(region.text).strip()
            )
            return OcrResult(image_sha256=image.sha256, regions=regions)

    async def _ensure_loaded(self) -> tuple[Any, Any, Any]:
        from ..vendor.manga_image_translator.manga_translator.detection.default import (
            DefaultDetector,
        )
        from ..vendor.manga_image_translator.manga_translator.ocr.model_48px import (
            Model48pxOCR,
        )
        from ..vendor.manga_image_translator.manga_translator.textline_merge import (
            dispatch,
        )

        self._verify_required_models()
        if self._detector is None:
            DefaultDetector._MODEL_DIR = str(self._model_cache)
            self._detector = DefaultDetector()  # type: ignore[no-untyped-call]
            await self._detector.load(self._device)
        if self._recognizer is None:
            Model48pxOCR._MODEL_DIR = str(self._model_cache)
            self._recognizer = Model48pxOCR()  # type: ignore[no-untyped-call]
            await self._recognizer.load(self._device)
        return self._detector, self._recognizer, dispatch

    def _verify_required_models(self) -> None:
        manifest_path = Path(__file__).parents[1] / "models" / "manifest.json"
        manifest = ModelManifest.load(manifest_path)
        for filename, subdirectory in (
            ("detect-20241225.ckpt", "detection"),
            ("ocr_ar_48px.ckpt", "ocr"),
            ("alphabet-all-v7.txt", "ocr"),
        ):
            verify_model(self._model_cache / subdirectory / filename, manifest.artifact(filename))


def _decode_rgb(content: bytes) -> Any:
    import numpy as np

    with Image.open(io.BytesIO(content)) as image:
        return np.asarray(image.convert("RGB"))


def _simple_reading_order(regions: list[Any]) -> list[Any]:
    def sort_key(region: Any) -> tuple[int, float, float]:
        x1, y1, x2, y2 = (float(value) for value in region.xyxy)
        vertical = (y2 - y1) > (x2 - x1)
        if vertical:
            return (0, -((x1 + x2) / 2), (y1 + y2) / 2)
        return (1, (y1 + y2) / 2, (x1 + x2) / 2)

    return sorted(regions, key=sort_key)
