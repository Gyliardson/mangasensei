from __future__ import annotations

import numpy as np
import pytest

from mangasensei.ocr.adapter.recognizer_48px import (
    _copy_recognition,
    _expand_short_axis,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)


def _quadrilateral(points: list[list[int]]) -> Quadrilateral:
    return Quadrilateral(np.asarray(points, dtype=np.int64), "", 0.9)


def _axis_lengths(line: Quadrilateral) -> tuple[float, float]:
    first, second, third, fourth = [
        np.asarray(point, dtype=np.float64) for point in line.structure
    ]
    long_length = float(np.linalg.norm(second - first))
    short_length = float(np.linalg.norm(fourth - third))
    return long_length, short_length


def test_vertical_recognition_context_expands_only_short_axis() -> None:
    source = _quadrilateral([[100, 100], [120, 100], [120, 300], [100, 300]])

    expanded = _expand_short_axis(
        source,
        factor=1.16,
        image_width=1000,
        image_height=1000,
    )

    assert source.xyxy == (100, 100, 120, 300)
    assert expanded.xyxy == (98, 100, 122, 300)
    source_long, source_short = _axis_lengths(source)
    expanded_long, expanded_short = _axis_lengths(expanded)
    assert expanded_long == pytest.approx(source_long)
    assert expanded_short == pytest.approx(source_short * 1.16, abs=1.0)


def test_rotated_recognition_context_preserves_long_axis() -> None:
    source = _quadrilateral([[180, 92], [200, 98], [140, 308], [120, 302]])

    expanded = _expand_short_axis(
        source,
        factor=1.16,
        image_width=1000,
        image_height=1000,
    )

    source_long, source_short = _axis_lengths(source)
    expanded_long, expanded_short = _axis_lengths(expanded)
    assert expanded_long == pytest.approx(source_long, abs=1.5)
    assert expanded_short == pytest.approx(source_short * 1.16, abs=1.5)


def test_recognition_result_is_copied_back_without_geometry_change() -> None:
    detector_line = _quadrilateral([[100, 100], [120, 100], [120, 300], [100, 300]])
    recognized_line = _quadrilateral([[98, 100], [122, 100], [122, 300], [98, 300]])
    recognized_line.text = "医師"
    recognized_line.prob = 0.98
    recognized_line.fg_r = 1
    recognized_line.fg_g = 2
    recognized_line.fg_b = 3
    recognized_line.bg_r = 4
    recognized_line.bg_g = 5
    recognized_line.bg_b = 6
    recognized_line.assigned_direction = "v"

    _copy_recognition(recognized_line, detector_line)

    assert detector_line.xyxy == (100, 100, 120, 300)
    assert detector_line.text == "医師"
    assert detector_line.prob == pytest.approx(0.98)
    assert detector_line.fg_colors.tolist() == [1, 2, 3]
    assert detector_line.bg_colors.tolist() == [4, 5, 6]
    assert detector_line.assigned_direction == "v"
