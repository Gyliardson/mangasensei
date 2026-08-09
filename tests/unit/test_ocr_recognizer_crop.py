from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from mangasensei.ocr.adapter.recognizer_48px import (
    MangaSenseiModel48pxOCR,
    _copy_quadrilateral,
    _copy_recognition,
    _expand_short_axis,
    _RecognitionQuadrilateral,
)
from mangasensei.ocr.adapter.recognizer_contract import (
    RECOGNITION_BATCH_CONFIRMATION_CEILING,
    RECOGNITION_SHORT_AXIS_CONTEXT,
    RECOGNITION_SHORT_EDGE_PAD_RATIO,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.ocr.model_48px import (
    Model48pxOCR,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.generic import (
    Quadrilateral,
)


def _quadrilateral(points: list[list[int]]) -> Quadrilateral:
    return Quadrilateral(np.asarray(points, dtype=np.int64), "", 0.9)


def _unloaded_recognizer() -> MangaSenseiModel48pxOCR:
    recognizer = object.__new__(MangaSenseiModel48pxOCR)
    recognizer._short_axis_context = 1.0
    recognizer._batch_confirmation_ceiling = RECOGNITION_BATCH_CONFIRMATION_CEILING
    return recognizer


@pytest.mark.parametrize(
    ("points", "direction"),
    [
        ([[1, 1], [3, 1], [3, 3], [1, 3]], "h"),
        ([[1, 1], [3, 1], [3, 4], [1, 4]], "v"),
    ],
)
def test_recognizer_crop_keeps_maximum_detector_pixel_in_source(
    points: list[list[int]],
    direction: str,
) -> None:
    image = np.full((6, 6, 3), 255, dtype=np.uint8)
    line = _RecognitionQuadrilateral(np.asarray(points, dtype=np.int64), "", 0.9)

    crop = line.get_transformed_region(image, direction, 3)

    assert crop.size > 0
    assert np.all(crop == 255), (
        "the perspective crop sampled outside the detector quadrilateral's inclusive pixel bounds"
    )


def test_recognizer_crop_clips_points_to_last_valid_image_pixel() -> None:
    image = np.full((5, 5, 3), 255, dtype=np.uint8)
    line = _RecognitionQuadrilateral(
        np.asarray([[3, 1], [5, 1], [5, 3], [3, 3]], dtype=np.int64),
        "",
        0.9,
    )

    crop = line.get_transformed_region(image, "h", 3)

    assert crop.size > 0
    assert np.all(crop == 255), (
        "image-edge detector geometry introduced a synthetic dark recognizer border"
    )


def test_recognizer_context_contract_is_symmetric_short_edge_padding() -> None:
    assert pytest.approx(0.08) == RECOGNITION_SHORT_EDGE_PAD_RATIO
    assert pytest.approx(
        1.0 + 2.0 * RECOGNITION_SHORT_EDGE_PAD_RATIO
    ) == RECOGNITION_SHORT_AXIS_CONTEXT
    assert pytest.approx(0.5) == RECOGNITION_BATCH_CONFIRMATION_CEILING


def test_recognizer_context_expands_only_local_short_axis() -> None:
    source = _quadrilateral([[100, 100], [120, 100], [120, 300], [100, 300]])

    expanded = _expand_short_axis(
        source,
        factor=RECOGNITION_SHORT_AXIS_CONTEXT,
        image_width=1000,
        image_height=1000,
    )

    assert expanded.xyxy[1] == source.xyxy[1]
    assert expanded.xyxy[3] == source.xyxy[3]
    assert expanded.xyxy[0] < source.xyxy[0]
    assert expanded.xyxy[2] > source.xyxy[2]


def test_crop_safe_copy_preserves_detector_geometry() -> None:
    source = _quadrilateral([[100, 100], [120, 100], [120, 300], [100, 300]])

    copied = _copy_quadrilateral(source)

    assert copied.xyxy == source.xyxy
    assert np.array_equal(copied.pts, source.pts)
    assert isinstance(copied, _RecognitionQuadrilateral)


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


@pytest.mark.asyncio
async def test_low_confidence_batch_only_result_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer = _unloaded_recognizer()
    detector_line = _quadrilateral([[2, 2], [8, 2], [8, 12], [2, 12]])
    image = np.full((16, 16, 3), 255, dtype=np.uint8)
    calls = 0

    async def fake_parent_recognize(
        self: Model48pxOCR,
        image: np.ndarray,
        textlines: list[Quadrilateral],
        config: Any,
        verbose: bool = False,
    ) -> list[Quadrilateral]:
        nonlocal calls
        del self, image, config, verbose
        calls += 1
        if calls == 1:
            textlines[0].text = "OOO"
            textlines[0].prob = 0.3
            return textlines
        return []

    monkeypatch.setattr(Model48pxOCR, "recognize", fake_parent_recognize)

    result = await recognizer.recognize(image, [detector_line], object())

    assert result == []
    assert calls == 2
    assert detector_line.text == ""


@pytest.mark.asyncio
async def test_low_confidence_result_keeps_batch_text_when_isolation_confirms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer = _unloaded_recognizer()
    detector_line = _quadrilateral([[2, 2], [8, 2], [8, 12], [2, 12]])
    image = np.full((16, 16, 3), 255, dtype=np.uint8)
    calls = 0

    async def fake_parent_recognize(
        self: Model48pxOCR,
        image: np.ndarray,
        textlines: list[Quadrilateral],
        config: Any,
        verbose: bool = False,
    ) -> list[Quadrilateral]:
        nonlocal calls
        del self, image, config, verbose
        calls += 1
        if calls == 1:
            textlines[0].text = "総合案内"
            textlines[0].prob = 0.3
        else:
            textlines[0].text = "総合案內"
            textlines[0].prob = 0.8
        return textlines

    monkeypatch.setattr(Model48pxOCR, "recognize", fake_parent_recognize)

    result = await recognizer.recognize(image, [detector_line], object())

    assert result == [detector_line]
    assert calls == 2
    assert detector_line.text == "総合案内"
    assert detector_line.prob == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_high_confidence_result_skips_isolated_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recognizer = _unloaded_recognizer()
    detector_line = _quadrilateral([[2, 2], [8, 2], [8, 12], [2, 12]])
    image = np.full((16, 16, 3), 255, dtype=np.uint8)
    calls = 0

    async def fake_parent_recognize(
        self: Model48pxOCR,
        image: np.ndarray,
        textlines: list[Quadrilateral],
        config: Any,
        verbose: bool = False,
    ) -> list[Quadrilateral]:
        nonlocal calls
        del self, image, config, verbose
        calls += 1
        textlines[0].text = "医師"
        textlines[0].prob = 0.9
        return textlines

    monkeypatch.setattr(Model48pxOCR, "recognize", fake_parent_recognize)

    result = await recognizer.recognize(image, [detector_line], object())

    assert result == [detector_line]
    assert calls == 1
    assert detector_line.text == "医師"
