from __future__ import annotations

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import region_from_upstream


class UpstreamRegion:
    xyxy = (100, 200, 400, 700)
    min_rect = (((100, 200), (400, 200), (400, 700), (100, 700)),)
    angle = 0.0
    prob = 0.875
    text = "猫です"


def test_upstream_region_is_converted_without_renderer_fields() -> None:
    dimensions = PageDimensions(width=1000, height=2000)

    first = region_from_upstream(
        UpstreamRegion(),
        image_sha256="a" * 64,
        dimensions=dimensions,
        reading_order=0,
    )
    second = region_from_upstream(
        UpstreamRegion(),
        image_sha256="a" * 64,
        dimensions=dimensions,
        reading_order=0,
    )

    assert first == second
    assert first.bbox.model_dump() == {"x": 100, "y": 200, "width": 300, "height": 500}
    assert first.normalized_bbox.model_dump() == {
        "x": 0.1,
        "y": 0.1,
        "width": 0.3,
        "height": 0.25,
    }
    assert first.polygon == ((100, 200), (400, 200), (400, 700), (100, 700))
    assert first.japanese_text == "猫です"
    assert first.detector == "default"
    assert first.recognizer == "48px"
