from __future__ import annotations

from mangasensei.ocr.diagnostics.reading_order_v2_contracts import ExperimentRegion
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.utils.textblock import TextBlock

from .contracts import ArmPageInput

_TEXT_SENTINEL = "qualification-fixture"
_PROB_SENTINEL = 0.5


def build_textblock_regions(page: ArmPageInput) -> tuple[ExperimentRegion, ...]:
    result: list[ExperimentRegion] = []
    for fixture in page.regions:
        block = TextBlock(
            lines=[list(line) for line in fixture.lines],
            texts=[_TEXT_SENTINEL],
            angle=fixture.angle,
            target_lang="",
            direction="auto",
            prob=_PROB_SENTINEL,
        )
        if block.text != _TEXT_SENTINEL or float(block.prob) != _PROB_SENTINEL:
            raise AssertionError("fixture sentinel changed during construction")
        result.append(ExperimentRegion(fixture.region_id, fixture.source_index, block))
    return tuple(result)
