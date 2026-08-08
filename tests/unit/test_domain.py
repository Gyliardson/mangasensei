from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mangasensei.domain.models import BoundingBox, OcrRegion, PageDimensions


def test_bounding_box_normalizes_against_original_dimensions() -> None:
    dimensions = PageDimensions(width=1000, height=2000)
    box = BoundingBox(x=100, y=400, width=300, height=500)

    normalized = box.normalize(dimensions)

    assert normalized.model_dump() == {
        "x": 0.1,
        "y": 0.2,
        "width": 0.3,
        "height": 0.25,
    }


def test_region_contract_is_immutable_and_keeps_raw_and_corrected_text_separate() -> None:
    region = OcrRegion(
        id="region-001",
        page_dimensions=PageDimensions(width=1000, height=2000),
        bbox=BoundingBox(x=100, y=400, width=300, height=500),
        polygon=((100, 400), (400, 400), (400, 900), (100, 900)),
        angle=0.0,
        confidence=0.94,
        raw_text="猫です",
        corrected_text=None,
        reading_order=0,
        detector="default",
        recognizer="48px",
        upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
        created_at=datetime(2026, 8, 7, tzinfo=UTC),
    )

    assert region.normalized_bbox.x == 0.1
    assert region.raw_text == "猫です"
    assert region.corrected_text is None
    with pytest.raises(ValidationError):
        region.raw_text = "mutated"  # type: ignore[misc]


def test_bounding_box_must_fit_inside_page() -> None:
    with pytest.raises(ValidationError, match="outside page dimensions"):
        OcrRegion(
            id="region-001",
            page_dimensions=PageDimensions(width=100, height=100),
            bbox=BoundingBox(x=80, y=80, width=30, height=30),
            polygon=None,
            angle=0,
            confidence=1,
            raw_text="猫",
            corrected_text=None,
            reading_order=0,
            detector="default",
            recognizer="48px",
            upstream_commit="95227a2bb0fd306cd4f0c104d57284026f991b3a",
            created_at=datetime.now(UTC),
        )
