from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
)
from mangasensei.ocr.contracts import OcrImage, OcrRegionResult, OcrResult

pytestmark = [
    pytest.mark.ocr_smoke,
    pytest.mark.skipif(
        os.environ.get("MANGASENSEI_RUN_OCR_SMOKE") != "1",
        reason="set MANGASENSEI_RUN_OCR_SMOKE=1 to load the real OCR models",
    ),
]

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "ocr" / "real_manga" / "black_jack"
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
PAGE9_PATH = "v01/black_jack_v01_pdf009.jpg"
PAGE9_TARGET_ZONE = (160, 290, 930, 1220)
PAGE9_OVERLAY3_ZONE = (130, 285, 455, 745)
PAGE9_OVERLAY3_SCALE = 0.9
PAGE9_OVERLAY3_ANCHOR = "国家試験に合格しなければいけない"
SHORT_TEXT_CASES = (
    (
        "v01/black_jack_v01_pdf073.jpg",
        "うむ",
        (500, 620, 250, 420),
    ),
    (
        "v01/black_jack_v01_pdf090.jpg",
        "はい",
        (1030, 1210, 160, 480),
    ),
)
CONTEXT_RECALL_CASES = (
    (
        "v01/black_jack_v01_pdf171.jpg",
        "※ステント＝心臓の血管",
        (80, 220, 650, 1800),
    ),
)
SHORT_TEXT_PATHS = {case[0] for case in SHORT_TEXT_CASES}
CONTEXT_RECALL_PATHS = {case[0] for case in CONTEXT_RECALL_CASES}
REVIEWED_TARGET_PATHS = SHORT_TEXT_PATHS | CONTEXT_RECALL_PATHS | {PAGE9_PATH}


@pytest.mark.asyncio
async def test_licensed_manga_short_vertical_text_recall_is_repeatable() -> None:
    """Protect reviewed short-text recall across repeated identical CPU inference."""
    engine = _real_engine()
    repeat_runs = _repeat_runs()

    for relative_path, expected_text, expected_center_zone in SHORT_TEXT_CASES:
        observations: list[tuple[int, tuple[tuple[float, float], ...]]] = []
        for _ in range(repeat_runs):
            result = await _analyze_fixture(engine, relative_path)
            matches = [region for region in result.regions if expected_text in region.japanese_text]
            in_zone = tuple(
                _center(region)
                for region in matches
                if _center_in_zone(region, expected_center_zone)
            )

            assert matches, (
                f"expected reviewed short vertical target in {relative_path}; "
                f"region_count={len(result.regions)}"
            )
            assert 4 <= len(result.regions) <= 32, (
                f"unexpected region-count shift for {relative_path}: {len(result.regions)}"
            )
            assert in_zone, (
                f"reviewed short target was recognized outside its fixture area in {relative_path}"
            )
            observations.append((len(result.regions), in_zone))

        print(
            "OCR_REPEATABILITY "
            f"fixture={relative_path} runs={repeat_runs} observations={observations!r}"
        )


@pytest.mark.asyncio
async def test_licensed_manga_recognizer_context_preserves_detector_complete_text() -> None:
    """Protect a second real page from the same crop-edge recognition failure class."""
    engine = _real_engine()

    for relative_path, expected_text, expected_center_zone in CONTEXT_RECALL_CASES:
        result = await _analyze_fixture(engine, relative_path)
        matches = [
            region
            for region in result.regions
            if expected_text in region.japanese_text
            and _center_in_zone(region, expected_center_zone)
        ]

        assert matches, (
            f"reviewed detector-complete text was lost in {relative_path}; "
            f"region_count={len(result.regions)}"
        )
        assert 4 <= len(result.regions) <= 32, (
            f"unexpected region-count shift for {relative_path}: {len(result.regions)}"
        )


@pytest.mark.asyncio
async def test_licensed_page9_two_line_target_survives_narrow_recognizer_batch() -> None:
    """Protect the reviewed page-9 target from recognizer batch-composition loss."""
    engine = _real_engine()
    detector, recognizer, merge, _ = await engine._ensure_loaded()
    image_path = FIXTURE_ROOT / PAGE9_PATH
    content = image_path.read_bytes()
    pixels = _decode_rgb(content)
    with Image.open(image_path) as source:
        width, height = source.width, source.height

    textlines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    target_lines = [
        line for line in textlines if _upstream_center_in_zone(line, PAGE9_TARGET_ZONE)
    ]
    assert len(target_lines) == 2, (
        "reviewed page-9 detector target changed before recognition; "
        f"target_candidate_count={len(target_lines)} detector_candidate_count={len(textlines)}"
    )

    observations: list[tuple[tuple[int, int, int, int], ...]] = []
    for _ in range(_repeat_runs()):
        recognized = await recognizer.recognize(
            pixels,
            copy.deepcopy(target_lines),
            engine._ocr_config,
            _RECOGNIZER_FLAG,
        )
        recognized = [line for line in recognized if str(line.text).strip()]
        assert len(recognized) == 2, (
            "both reviewed page-9 detector lines must survive the production recognizer "
            f"when they form the complete narrow batch; recognized_count={len(recognized)}"
        )

        merged = await merge(recognized, width, height)
        merged_extents = tuple(tuple(int(value) for value in block.xyxy) for block in merged)
        assert any(_spans_page9_target(extent) for extent in merged_extents), (
            "recognized page-9 lines did not merge into geometry spanning the reviewed target; "
            f"merged_extents={merged_extents!r}"
        )
        observations.append(merged_extents)

    result = await _analyze_fixture(engine, PAGE9_PATH)
    assert 4 <= len(result.regions) <= 32, (
        f"unexpected page-9 region-count shift: {len(result.regions)}"
    )
    assert any(_region_spans_page9_target(region) for region in result.regions), (
        "full production OCR did not preserve the reviewed page-9 target geometry"
    )
    print(
        "OCR_PAGE9_BATCH_CONTEXT "
        f"runs={_repeat_runs()} detector_target_count={len(target_lines)} "
        f"merged_extents={observations!r} final_region_count={len(result.regions)}"
    )


@pytest.mark.asyncio
async def test_licensed_page9_three_column_dialogue_survives_resampling() -> None:
    """Protect a detector-complete dialogue block from recognizer crop-edge loss."""
    engine = _real_engine()
    detector, recognizer, merge, _ = await engine._ensure_loaded()
    image_path = FIXTURE_ROOT / PAGE9_PATH
    with Image.open(image_path) as source:
        resized = source.convert("RGB").resize(
            (
                round(source.width * PAGE9_OVERLAY3_SCALE),
                round(source.height * PAGE9_OVERLAY3_SCALE),
            ),
            Image.Resampling.LANCZOS,
        )

    encoded = io.BytesIO()
    resized.save(encoded, format="PNG", optimize=True)
    content = encoded.getvalue()
    pixels = _decode_rgb(content)
    width, height = resized.size
    scaled_zone = _scale_zone(PAGE9_OVERLAY3_ZONE, PAGE9_OVERLAY3_SCALE)

    textlines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    target_lines = [
        line for line in textlines if _upstream_center_in_zone(line, scaled_zone)
    ]
    assert len(target_lines) == 3, (
        "reviewed page-9 three-column detector target changed before recognition; "
        f"target_candidate_count={len(target_lines)} detector_candidate_count={len(textlines)}"
    )

    recognized = await recognizer.recognize(
        pixels,
        copy.deepcopy(target_lines),
        engine._ocr_config,
        _RECOGNIZER_FLAG,
    )
    recognized = [line for line in recognized if str(line.text).strip()]
    assert len(recognized) == 3, (
        "all three detector-complete page-9 dialogue columns must survive production "
        f"recognition after ordinary resampling; recognized_count={len(recognized)}"
    )

    merged = await merge(recognized, width, height)
    merged_extents = tuple(tuple(int(value) for value in block.xyxy) for block in merged)
    assert any(_spans_scaled_page9_overlay3(extent) for extent in merged_extents), (
        "resampled page-9 dialogue did not merge across all three reviewed columns; "
        f"merged_extents={merged_extents!r}"
    )

    result = await engine.analyze(
        OcrImage(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="image/png",
            dimensions=PageDimensions(width=width, height=height),
        )
    )
    _write_visual_audit("black_jack_v01_pdf009-scale-090", content, result)
    target_regions = [
        region for region in result.regions if _center_in_zone(region, scaled_zone)
    ]
    assert any(
        PAGE9_OVERLAY3_ANCHOR in region.japanese_text for region in target_regions
    ), "full production OCR lost the reading-first column of the reviewed page-9 dialogue"
    assert any(_region_spans_scaled_page9_overlay3(region) for region in target_regions), (
        "full production OCR truncated the reviewed page-9 three-column dialogue geometry"
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("MANGASENSEI_OCR_FULL_CORPUS") != "1",
    reason="full licensed corpus is reserved for the deeper OCR assurance tier",
)
async def test_licensed_manga_full_corpus_stays_within_catastrophic_region_bounds() -> None:
    """Catch full-corpus detection collapse/explosion without transcript ground truth."""
    engine = _real_engine()
    manifest = _load_manifest()

    for fixture in manifest["fixtures"]:
        relative_path = _manifest_string(fixture, "file")
        if relative_path in REVIEWED_TARGET_PATHS:
            continue

        result = await _analyze_fixture(engine, relative_path)
        assert 2 <= len(result.regions) <= 32, (
            f"catastrophic region-count shift for {relative_path}: {len(result.regions)}"
        )
        print(f"OCR_CORPUS fixture={relative_path} region_count={len(result.regions)}")


def _real_engine() -> MangaImageTranslatorEngine:
    return MangaImageTranslatorEngine(
        model_cache=Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models")),
        device="cpu",
    )


async def _analyze_fixture(
    engine: MangaImageTranslatorEngine,
    relative_path: str,
) -> OcrResult:
    image_path = FIXTURE_ROOT / relative_path
    content = image_path.read_bytes()
    with Image.open(image_path) as source:
        dimensions = PageDimensions(width=source.width, height=source.height)

    result = await engine.analyze(
        OcrImage(
            content=content,
            sha256=hashlib.sha256(content).hexdigest(),
            media_type="image/jpeg",
            dimensions=dimensions,
        )
    )
    _write_visual_audit(Path(relative_path).stem, content, result)
    return result


def _write_visual_audit(label: str, content: bytes, result: OcrResult) -> None:
    root_value = os.environ.get("MANGASENSEI_OCR_ARTIFACT_DIR")
    if not root_value:
        return

    root = Path(root_value)
    root.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(content)) as source:
        rendered = source.convert("RGB")
    draw = ImageDraw.Draw(rendered)
    line_width = max(2, round(max(rendered.size) / 700))

    regions: list[dict[str, Any]] = []
    for index, region in enumerate(result.regions):
        x1 = region.bbox.x
        y1 = region.bbox.y
        x2 = x1 + region.bbox.width
        y2 = y1 + region.bbox.height
        draw.rectangle((x1, y1, x2, y2), outline=(210, 25, 55), width=line_width)
        label_x = max(0, min(x1, rendered.width - 22))
        label_y = max(0, y1 - 15)
        draw.rectangle((label_x, label_y, label_x + 22, label_y + 15), fill=(255, 255, 255))
        draw.text((label_x + 3, label_y + 1), str(index), fill=(0, 0, 0))
        regions.append(
            {
                "index": index,
                "reading_order": region.reading_order,
                "bbox": region.bbox.model_dump(),
                "normalized_bbox": region.normalized_bbox.model_dump(),
                "polygon": region.polygon,
                "confidence": region.confidence,
                "japanese_text": region.japanese_text,
            }
        )

    rendered.save(root / f"{label}.jpg", format="JPEG", quality=92)
    payload = {
        "label": label,
        "image_sha256": result.image_sha256,
        "dimensions": result.regions[0].dimensions.model_dump() if result.regions else None,
        "region_count": len(result.regions),
        "regions": regions,
    }
    (root / f"{label}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _repeat_runs() -> int:
    value = int(os.environ.get("MANGASENSEI_OCR_REPEAT_RUNS", "1"))
    if not 1 <= value <= 5:
        raise ValueError("MANGASENSEI_OCR_REPEAT_RUNS must be between 1 and 5")
    return value


def _load_manifest() -> dict[str, Any]:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    fixtures = raw.get("fixtures")
    assert isinstance(fixtures, list)
    return raw


def _manifest_string(entry: object, key: str) -> str:
    assert isinstance(entry, dict)
    value = entry[key]
    assert isinstance(value, str)
    return value


def _center(region: OcrRegionResult) -> tuple[float, float]:
    return (
        region.bbox.x + region.bbox.width / 2,
        region.bbox.y + region.bbox.height / 2,
    )


def _center_in_zone(region: OcrRegionResult, zone: tuple[int, int, int, int]) -> bool:
    min_x, max_x, min_y, max_y = zone
    center_x, center_y = _center(region)
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y


def _upstream_center_in_zone(region: Any, zone: tuple[int, int, int, int]) -> bool:
    min_x, max_x, min_y, max_y = zone
    x1, y1, x2, y2 = (float(value) for value in region.xyxy)
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    return min_x <= center_x <= max_x and min_y <= center_y <= max_y


def _scale_zone(
    zone: tuple[int, int, int, int], scale: float
) -> tuple[int, int, int, int]:
    return tuple(round(value * scale) for value in zone)  # type: ignore[return-value]


def _spans_page9_target(extent: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = extent
    return x1 <= 190 and y1 <= 970 and x2 >= 250 and y2 >= 1180


def _region_spans_page9_target(region: OcrRegionResult) -> bool:
    return _spans_page9_target(
        (
            region.bbox.x,
            region.bbox.y,
            region.bbox.x + region.bbox.width,
            region.bbox.y + region.bbox.height,
        )
    )


def _spans_scaled_page9_overlay3(extent: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = extent
    return x1 <= 145 and y1 <= 450 and x2 >= 220 and y2 >= 625


def _region_spans_scaled_page9_overlay3(region: OcrRegionResult) -> bool:
    return _spans_scaled_page9_overlay3(
        (
            region.bbox.x,
            region.bbox.y,
            region.bbox.x + region.bbox.width,
            region.bbox.y + region.bbox.height,
        )
    )
