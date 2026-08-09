"""MangaSensei boundary fixes for the vendored 48px recognizer."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..vendor.manga_image_translator.manga_translator.ocr.model_48px import Model48pxOCR
from ..vendor.manga_image_translator.manga_translator.utils.generic import Quadrilateral


class MangaSenseiModel48pxOCR(Model48pxOCR):
    """Recognize detector geometry with an inclusive, pixel-valid perspective crop."""

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
    """Quadrilateral whose recognizer crop keeps every referenced source pixel valid."""

    def get_transformed_region(
        self,
        img: np.ndarray,
        direction: str,
        textheight: int,
    ) -> np.ndarray:
        [l1a, l1b, l2a, l2b] = [point.astype(np.float32) for point in self.structure]
        vertical_vector = l1b - l1a
        horizontal_vector = l2b - l2a
        ratio = float(np.linalg.norm(vertical_vector) / np.linalg.norm(horizontal_vector))

        source_points = self.pts.astype(np.int64).copy()
        image_height, image_width = img.shape[:2]
        if image_width <= 0 or image_height <= 0:
            raise ValueError("recognizer source image must be non-empty")

        source_points[:, 0] = np.clip(source_points[:, 0], 0, image_width - 1)
        source_points[:, 1] = np.clip(source_points[:, 1], 0, image_height - 1)
        x1 = int(source_points[:, 0].min())
        y1 = int(source_points[:, 1].min())
        x2 = int(source_points[:, 0].max())
        y2 = int(source_points[:, 1].max())

        # Detector points are pixel coordinates and therefore include their maximum
        # coordinate. NumPy's upper slice bound is exclusive, so include x2/y2 here.
        # Otherwise the homography can reference x == crop_width or y == crop_height,
        # forcing warpPerspective to extrapolate beyond the source crop.
        cropped = img[y1 : y2 + 1, x1 : x2 + 1]
        source_points[:, 0] -= x1
        source_points[:, 1] -= y1
        source = source_points.astype(np.float32)

        if direction == "h":
            height = max(int(textheight), 2)
            width = max(int(round(textheight / ratio)), 2)
            destination = np.asarray(
                [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                dtype=np.float32,
            )
            matrix, _ = cv2.findHomography(source, destination, cv2.RANSAC, 5.0)
            if matrix is None:
                raise RuntimeError("could not construct recognizer perspective transform")
            return cv2.warpPerspective(cropped, matrix, (width, height))

        if direction == "v":
            width = max(int(textheight), 2)
            height = max(int(round(textheight * ratio)), 2)
            destination = np.asarray(
                [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
                dtype=np.float32,
            )
            matrix, _ = cv2.findHomography(source, destination, cv2.RANSAC, 5.0)
            if matrix is None:
                raise RuntimeError("could not construct recognizer perspective transform")
            region = cv2.warpPerspective(cropped, matrix, (width, height))
            return cv2.rotate(region, cv2.ROTATE_90_COUNTERCLOCKWISE)

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
