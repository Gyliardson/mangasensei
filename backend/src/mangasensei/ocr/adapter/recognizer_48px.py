"""MangaSensei boundary fixes for the vendored 48px recognizer."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..vendor.manga_image_translator.manga_translator.ocr.model_48px import Model48pxOCR
from ..vendor.manga_image_translator.manga_translator.utils.generic import Quadrilateral
from .recognizer_contract import (
    RECOGNITION_BATCH_CONFIRMATION_CEILING,
    RECOGNITION_SHORT_AXIS_CONTEXT,
)


class MangaSenseiModel48pxOCR(Model48pxOCR):
    """Recognize with calibrated source context and pixel-valid crop bounds."""

    def __init__(
        self,
        *args: Any,
        short_axis_context: float = RECOGNITION_SHORT_AXIS_CONTEXT,
        batch_confirmation_ceiling: float = RECOGNITION_BATCH_CONFIRMATION_CEILING,
        **kwargs: Any,
    ) -> None:
        if short_axis_context < 1.0:
            raise ValueError("short_axis_context must be at least 1.0")
        if not 0.0 <= batch_confirmation_ceiling <= 1.0:
            raise ValueError("batch_confirmation_ceiling must be between 0.0 and 1.0")
        self._short_axis_context = short_axis_context
        self._batch_confirmation_ceiling = batch_confirmation_ceiling
        super().__init__(*args, **kwargs)  # type: ignore[no-untyped-call]

    async def recognize(
        self,
        image: np.ndarray,
        textlines: list[Quadrilateral],
        config: Any,
        verbose: bool = False,
    ) -> list[Quadrilateral]:
        if not textlines:
            return await super().recognize(image, textlines, config, verbose)

        height, width = image.shape[:2]
        expanded: list[Quadrilateral] = [
            _expand_short_axis(
                line,
                factor=self._short_axis_context,
                image_width=width,
                image_height=height,
            )
            for line in textlines
        ]
        confirmation_inputs = [_copy_quadrilateral(line) for line in expanded]
        source_index_by_object = {id(line): index for index, line in enumerate(expanded)}
        source_index_by_geometry = {
            _geometry_key(line): index for index, line in enumerate(expanded)
        }

        recognized = await super().recognize(image, expanded, config, verbose)
        restored: list[Quadrilateral] = []
        for recognized_line in recognized:
            source_index = source_index_by_object.get(id(recognized_line))
            if source_index is None:
                source_index = source_index_by_geometry.get(_geometry_key(recognized_line))
            if source_index is None:
                raise RuntimeError("recognized line no longer matches a detector candidate")

            if float(recognized_line.prob) < self._batch_confirmation_ceiling:
                confirmation = await super().recognize(
                    image,
                    [confirmation_inputs[source_index]],
                    config,
                    False,
                )
                if not confirmation:
                    continue

            original = textlines[source_index]
            _copy_recognition(recognized_line, original)
            restored.append(original)
        return restored


class _RecognitionQuadrilateral(Quadrilateral):
    """Quadrilateral with an inclusive, pixel-valid recognizer source crop."""

    def get_transformed_region(
        self,
        img: np.ndarray,
        direction: str,
        textheight: int,
    ) -> np.ndarray:
        [l1a, l1b, l2a, l2b] = [point.astype(np.float32) for point in self.structure]
        vertical_vector = l1b - l1a
        horizontal_vector = l2b - l2a
        horizontal_norm = float(np.linalg.norm(horizontal_vector))
        if horizontal_norm == 0:
            raise ValueError("recognizer quadrilateral has zero short-axis extent")
        ratio = float(np.linalg.norm(vertical_vector) / horizontal_norm)
        if ratio <= 0:
            raise ValueError("recognizer quadrilateral has invalid aspect ratio")

        source = np.asarray(self.pts, dtype=np.int64).copy()
        image_height, image_width = img.shape[:2]
        if image_width <= 0 or image_height <= 0:
            raise ValueError("recognizer source image must be non-empty")
        source[:, 0] = np.clip(source[:, 0], 0, image_width - 1)
        source[:, 1] = np.clip(source[:, 1], 0, image_height - 1)

        x1 = int(source[:, 0].min())
        y1 = int(source[:, 1].min())
        x2 = int(source[:, 0].max())
        y2 = int(source[:, 1].max())
        cropped = img[y1 : y2 + 1, x1 : x2 + 1]
        source[:, 0] -= x1
        source[:, 1] -= y1
        source_points = source.astype(np.float32)

        if direction == "h":
            height = max(int(textheight), 2)
            width = max(int(round(textheight / ratio)), 2)
        elif direction == "v":
            width = max(int(textheight), 2)
            height = max(int(round(textheight * ratio)), 2)
        else:
            raise ValueError(f"unsupported recognizer direction: {direction}")

        destination = np.asarray(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
        matrix, _ = cv2.findHomography(source_points, destination, cv2.RANSAC, 5.0)
        if matrix is None:
            raise RuntimeError("could not construct recognizer perspective transform")
        region = cv2.warpPerspective(cropped, matrix, (width, height))
        if direction == "v":
            return cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return region


def _expand_short_axis(
    line: Quadrilateral,
    *,
    factor: float,
    image_width: int,
    image_height: int,
) -> Quadrilateral:
    """Add recognizer-only page context in the line-local short direction."""
    points = np.asarray(line.pts, dtype=np.float64)
    center = points.mean(axis=0)
    structure = [np.asarray(point, dtype=np.float64) for point in line.structure]
    if line.direction == "v":
        long_vector = structure[1] - structure[0]
    else:
        long_vector = structure[3] - structure[2]
    long_norm = float(np.linalg.norm(long_vector))
    if long_norm == 0:
        return _copy_quadrilateral(line, points)

    long_unit = long_vector / long_norm
    short_unit = np.asarray((-long_unit[1], long_unit[0]), dtype=np.float64)
    expanded_points: list[np.ndarray] = []
    for point in points:
        delta = point - center
        long_component = float(np.dot(delta, long_unit))
        short_component = float(np.dot(delta, short_unit)) * factor
        expanded_points.append(
            center + long_component * long_unit + short_component * short_unit
        )

    expanded = _copy_quadrilateral(line, np.rint(expanded_points).astype(np.int64))
    expanded.clip(image_width, image_height)  # type: ignore[no-untyped-call]
    return expanded


def _copy_quadrilateral(
    line: Quadrilateral,
    points: np.ndarray | None = None,
) -> _RecognitionQuadrilateral:
    copied = _RecognitionQuadrilateral(
        np.asarray(line.pts if points is None else points, dtype=np.int64).copy(),
        "",
        float(line.prob),
        int(line.fg_r),
        int(line.fg_g),
        int(line.fg_b),
        int(line.bg_r),
        int(line.bg_g),
        int(line.bg_b),
    )
    copied.assigned_direction = line.assigned_direction
    return copied


def _copy_recognition(source: Quadrilateral, target: Quadrilateral) -> None:
    target.text = str(source.text)
    target.prob = float(source.prob)
    target.fg_r = int(source.fg_r)
    target.fg_g = int(source.fg_g)
    target.fg_b = int(source.fg_b)
    target.bg_r = int(source.bg_r)
    target.bg_g = int(source.bg_g)
    target.bg_b = int(source.bg_b)
    target.assigned_direction = source.assigned_direction


def _geometry_key(line: Quadrilateral) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(line.pts))
