"""MangaSensei boundary fixes for the vendored 48px recognizer."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..vendor.manga_image_translator.manga_translator.ocr.model_48px import Model48pxOCR
from ..vendor.manga_image_translator.manga_translator.utils.generic import Quadrilateral


class MangaSenseiModel48pxOCR(Model48pxOCR):
    """Run recognition with bounded source context while preserving detector geometry."""

    def __init__(self, *args: Any, short_axis_context: float, **kwargs: Any) -> None:
        if short_axis_context < 1.0:
            raise ValueError("short_axis_context must be at least 1.0")
        self._short_axis_context = short_axis_context
        super().__init__(*args, **kwargs)

    async def recognize(
        self,
        image: np.ndarray,
        textlines: list[Quadrilateral],
        config: Any,
        verbose: bool = False,
    ) -> list[Quadrilateral]:
        if not textlines or self._short_axis_context == 1.0:
            return await super().recognize(image, textlines, config, verbose)

        height, width = image.shape[:2]
        expanded = [
            _expand_short_axis(
                line,
                factor=self._short_axis_context,
                image_width=width,
                image_height=height,
            )
            for line in textlines
        ]
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

            original = textlines[source_index]
            _copy_recognition(recognized_line, original)
            restored.append(original)
        return restored


def _expand_short_axis(
    line: Quadrilateral,
    *,
    factor: float,
    image_width: int,
    image_height: int,
) -> Quadrilateral:
    """Expand only recognizer source context in the line-local short direction."""
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
    expanded.clip(image_width, image_height)
    return expanded


def _copy_quadrilateral(line: Quadrilateral, points: np.ndarray) -> Quadrilateral:
    return Quadrilateral(
        points,
        "",
        float(line.prob),
        int(line.fg_r),
        int(line.fg_g),
        int(line.fg_b),
        int(line.bg_r),
        int(line.bg_g),
        int(line.bg_b),
    )


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
