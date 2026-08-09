from __future__ import annotations

import numpy as np
import pytest

from mangasensei.ocr.adapter.recognizer_48px import (
    _RecognitionQuadrilateral,
    _copy_quadrilateral,
    _copy_recognition,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)


def _quadrilateral(points: list[list[int]]) -> Quadrilateral:
    return Quadrilateral(np.asarray(points, dtype=np.int64), "", 0.9)


def test_recognizer_crop_keeps_maximum_detector_pixel_in_source() -> None:
    image = np.full((5, 5, 3), 255, dtype=np.uint8)
    line = _RecognitionQuadrilateral(
        np.asarray([[1, 1], [3, 1], [3, 3], [1, 3]], dtype=np.int64),
        "",
        0.9,
    )

    crop = line.get_transformed_region(image, "h", 3)

    assert crop.shape == (3, 3, 3)
    assert np.all(crop == 255), (
        "the perspective crop sampled outside the detector quadrilateral's inclusive pixel bounds"
    )


def test_recognizer_crop_clips_detector_points_to_last_valid_pixel() -> None:
    image = np.full((5, 5, 3), 255, dtype=np.uint8)
    line = _RecognitionQuadrilateral(
        np.asarray([[3, 1], [5, 1], [5, 3], [3, 3]], dtype=np.int64),
        "",
        0.9,
    )

    crop = line.get_transformed_region(image, "h", 3)

    assert crop.size > 0
    assert np.all(crop == 255), "clipped image-edge detector geometry introduced a synthetic dark border"


def test_crop_safe_copy_preserves_detector_geometry() -> None:
    source = _quadrilateral([[100, 100], [120, 100], [120, 300], [100, 300]])

    copied = _copy_quadrilateral(source)

    assert copied.xyxy == source.xyxy
    assert np.array_equal(copied.pts, source.pts)


def test_recognition_result_is_copied_back_without_geometry_change() -> None:
    detector_line = _quadrilateral([[100, 100], [120, 100], [120, 300], [100, 300]])
    recognized_line = _quadrilateral([[100, 100], [120, 100], [120, 300], [100, 300]])
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
