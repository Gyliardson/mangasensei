"""MangaSensei boundary fixes for the vendored 48px recognizer."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..vendor.manga_image_translator.manga_translator.ocr.model_48px import Model48pxOCR
from ..vendor.manga_image_translator.manga_translator.utils.generic import Quadrilateral

# The recognizer's first convolution is 7x7 with radius 3. Keep that radius as
# real source-image context around a detector-tight line in the normalized 48px
# short axis instead of letting glyph strokes sit against synthetic CNN padding.
RECOGNITION_SHORT_AXIS_PADDING = 3


class MangaSenseiModel48pxOCR(Model48pxOCR):
    """Recognize detector geometry with a pixel-valid, context-preserving warp."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
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

        crop_safe_lines = [_copy_quadrilateral(line) for line in textlines]
        source_index_by_object = {id(line): index for index, line in enumerate(crop_safe_lines)}
        source_index_by_geometry = {
            _geometry_key(line): index for index, line in enumerate(crop_safe_lines)
        }

        recognized = await super().recognize(image, crop_safe_lines, config, verbose)
        restored: list[Quadrilateral] = []
        for recognized_line in recognized:
            source_index = source_index_by_object.get(id(recognized_line))
            if source_index is None:
                source_index = source_index_by_geometry.get(_geometry_key(recognized_line))
            if source_index is None:
                raise RuntimeError("recognized line no longer matches a detector candidate")

            original = textlines[source_index]
            _copy_recognition(recognized_line, original)
            restored.append(original)
        return restored


class _RecognitionQuadrilateral(Quadrilateral):
    """Quadrilateral whose warp uses valid source pixels plus bounded real context."""

    def get_transformed_region(
        self,
        img: np.ndarray,
        direction: str,
        textheight: int,
    ) -> np.ndarray:
        image_height, image_width = img.shape[:2]
        if image_width <= 0 or image_height <= 0:
            raise ValueError("recognizer source image must be non-empty")

        source = np.asarray(self.pts, dtype=np.float32).copy()
        source[:, 0] = np.clip(source[:, 0], 0, image_width - 1)
        source[:, 1] = np.clip(source[:, 1], 0, image_height - 1)
        destination, width, height = _recognition_destination(
            self,
            direction=direction,
            textheight=textheight,
            padding=RECOGNITION_SHORT_AXIS_PADDING,
        )

        # Warp from the full source image. The upstream implementation first slices
        # to [y1:y2, x1:x2] but keeps homography points at x2/y2, so those inclusive
        # detector coordinates can become one-past-the-end of the sliced image.
        # Full-image coordinates remove that mismatch. BORDER_REPLICATE is reached
        # only when requested recognition context extends beyond the actual page.
        matrix = cv2.getPerspectiveTransform(source, destination)
        region = cv2.warpPerspective(
            img,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        if direction == "v":
            return cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return region


def _recognition_destination(
    line: Quadrilateral,
    *,
    direction: str,
    textheight: int,
    padding: int,
) -> tuple[np.ndarray, int, int]:
    if padding < 0 or padding * 2 >= textheight:
        raise ValueError("recognizer padding must leave a positive inner short axis")

    [l1a, l1b, l2a, l2b] = [point.astype(np.float32) for point in line.structure]
    vertical_vector = l1b - l1a
    horizontal_vector = l2b - l2a
    horizontal_norm = float(np.linalg.norm(horizontal_vector))
    if horizontal_norm == 0:
        raise ValueError("recognizer quadrilateral has zero short-axis extent")
    ratio = float(np.linalg.norm(vertical_vector) / horizontal_norm)
    if ratio <= 0:
        raise ValueError("recognizer quadrilateral has invalid aspect ratio")

    inner_short = textheight - 2 * padding
    if direction == "h":
        width = max(int(round(inner_short / ratio)), 2)
        height = textheight
        destination = np.asarray(
            [
                [0, padding],
                [width - 1, padding],
                [width - 1, padding + inner_short - 1],
                [0, padding + inner_short - 1],
            ],
            dtype=np.float32,
        )
        return destination, width, height

    if direction == "v":
        width = textheight
        height = max(int(round(inner_short * ratio)), 2)
        destination = np.asarray(
            [
                [padding, 0],
                [padding + inner_short - 1, 0],
                [padding + inner_short - 1, height - 1],
                [padding, height - 1],
            ],
            dtype=np.float32,
        )
        return destination, width, height

    raise ValueError(f"unsupported recognizer direction: {direction}")


def _copy_quadrilateral(line: Quadrilateral) -> _RecognitionQuadrilateral:
    copied = _RecognitionQuadrilateral(
        np.asarray(line.pts, dtype=np.int64).copy(),
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
