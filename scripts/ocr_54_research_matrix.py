from __future__ import annotations

import asyncio
import copy
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
)

FIXTURE_ROOT = Path("tests/fixtures/ocr/real_manga/black_jack/v01")
OUT = Path(os.environ.get("MANGASENSEI_OCR_RESEARCH_DIR", "var/ocr-54-research"))
MODEL_CACHE = Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models"))
FACTORS = (1.0, 1.04, 1.08, 1.12, 1.16, 1.20, 1.24)
PRODUCTION_THRESHOLD = 0.2
MAX_PRESSURE_FALLBACKS = 5

# Exact transcript anchors stay in the private diagnostic artifact only. Console output
# contains booleans/counts, never recognized manga text.
CASES = (
    {
        "label": "page9-scale-090",
        "file": "black_jack_v01_pdf009.jpg",
        "scale": 0.9,
        "target_zone": (130, 285, 455, 745),
        "anchor": "国家試験に合格しなければいけない",
        "kind": "anchor",
    },
    {
        "label": "page171",
        "file": "black_jack_v01_pdf171.jpg",
        "scale": 1.0,
        "target_zone": (70, 220, 650, 1800),
        "anchor": "※ステント＝心臓の血管",
        "kind": "anchor",
    },
    {
        "label": "page73",
        "file": "black_jack_v01_pdf073.jpg",
        "scale": 1.0,
        "target_zone": (500, 620, 250, 420),
        "anchor": "うむ",
        "kind": "anchor",
    },
    {
        "label": "page90",
        "file": "black_jack_v01_pdf090.jpg",
        "scale": 1.0,
        "target_zone": (1030, 1210, 160, 480),
        "anchor": "はい",
        "kind": "anchor",
    },
    {
        "label": "page21-pressure",
        "file": "black_jack_v01_pdf021.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
        "kind": "pressure",
    },
    {
        "label": "page41-pressure",
        "file": "black_jack_v01_pdf041.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
        "kind": "pressure",
    },
    {
        "label": "page145-pressure",
        "file": "black_jack_v01_pdf145.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
        "kind": "pressure",
    },
    {
        "label": "page201-pressure",
        "file": "black_jack_v01_pdf201.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
        "kind": "pressure",
    },
)


def _geometry_key(line: Any) -> str:
    return ";".join(f"{int(point[0])},{int(point[1])}" for point in np.asarray(line.pts))


def _center_in_zone(line: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = (float(value) for value in line.xyxy)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def _scale_zone(
    zone: tuple[int, int, int, int], factor: float
) -> tuple[int, int, int, int]:
    return tuple(round(value * factor) for value in zone)  # type: ignore[return-value]


def _load_pixels(case: dict[str, Any]) -> np.ndarray:
    source = FIXTURE_ROOT / str(case["file"])
    scale = float(case["scale"])
    if scale == 1.0:
        return _decode_rgb(source.read_bytes())
    with Image.open(source) as opened:
        resized = opened.convert("RGB").resize(
            (round(opened.width * scale), round(opened.height * scale)),
            Image.Resampling.LANCZOS,
        )
    return np.asarray(resized)


def _line_record(line: Any) -> dict[str, Any]:
    return {
        "geometry_key": _geometry_key(line),
        "xyxy": [int(value) for value in line.xyxy],
        "direction": str(line.direction),
        "probability": float(line.prob),
        "text": str(line.text),
        "text_length": len(str(line.text)),
        "survives_production_threshold": bool(
            str(line.text).strip() and float(line.prob) >= PRODUCTION_THRESHOLD
        ),
    }


def _source_crop(pixels: np.ndarray, line: Any) -> np.ndarray:
    x1, y1, x2, y2 = (int(value) for value in line.xyxy)
    height, width = pixels.shape[:2]
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)
    if str(line.direction) == "v":
        pad_x = max(4, round(box_w * 0.25))
        pad_y = max(3, round(box_h * 0.04))
    else:
        pad_x = max(3, round(box_w * 0.04))
        pad_y = max(4, round(box_h * 0.25))
    left = max(0, x1 - pad_x)
    top = max(0, y1 - pad_y)
    right = min(width, x2 + pad_x + 1)
    bottom = min(height, y2 + pad_y + 1)
    return pixels[top:bottom, left:right]


def _block_crop(pixels: np.ndarray, lines: list[Any]) -> np.ndarray:
    height, width = pixels.shape[:2]
    x1 = min(int(line.xyxy[0]) for line in lines)
    y1 = min(int(line.xyxy[1]) for line in lines)
    x2 = max(int(line.xyxy[2]) for line in lines)
    y2 = max(int(line.xyxy[3]) for line in lines)
    box_w = max(1, x2 - x1)
    box_h = max(1, y2 - y1)
    pad_x = max(6, round(box_w * 0.12))
    pad_y = max(6, round(box_h * 0.08))
    left = max(0, x1 - pad_x)
    top = max(0, y1 - pad_y)
    right = min(width, x2 + pad_x + 1)
    bottom = min(height, y2 + pad_y + 1)
    return pixels[top:bottom, left:right]


def _save_crop(label: str, suffix: str, crop: np.ndarray) -> str:
    crop_dir = OUT / "fallback-crops"
    crop_dir.mkdir(parents=True, exist_ok=True)
    relative = Path("fallback-crops") / f"{label}-{suffix}.png"
    Image.fromarray(crop).save(OUT / relative, format="PNG", optimize=True)
    return relative.as_posix()


def _choose_pressure_fallbacks(
    detector_lines: list[Any],
    observations: dict[str, dict[str, dict[str, Any]]],
) -> list[Any]:
    ranked: list[tuple[int, float, Any]] = []
    for line in detector_lines:
        key = _geometry_key(line)
        variants = [records[key] for records in observations.values() if key in records]
        texts = {str(item["text"]).strip() for item in variants if str(item["text"]).strip()}
        survives = {bool(item["survives_production_threshold"]) for item in variants}
        lengths = {int(item["text_length"]) for item in variants}
        disagreement = int(len(texts) > 1) + int(len(survives) > 1) + int(len(lengths) > 1)
        if disagreement == 0:
            continue
        confidence_span = max(float(item["probability"]) for item in variants) - min(
            float(item["probability"]) for item in variants
        )
        ranked.append((disagreement, confidence_span, line))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked[:MAX_PRESSURE_FALLBACKS]]


async def _run_case(
    engine: MangaImageTranslatorEngine,
    detector: Any,
    recognizer: Any,
    merge: Any,
    case: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pixels = _load_pixels(case)
    height, width = pixels.shape[:2]
    textlines, _, _ = await detector.detect(
        pixels,
        engine._detection_size,
        engine._text_threshold,
        engine._box_threshold,
        engine._unclip_ratio,
        *_DETECTOR_FLAGS,
    )
    zero_config = type(engine._ocr_config)(prob=0.0, ignore_bubble=0)
    factor_observations: dict[str, dict[str, dict[str, Any]]] = {}
    factor_summaries: dict[str, dict[str, Any]] = {}

    zone = case["target_zone"]
    scaled_zone = _scale_zone(zone, float(case["scale"])) if zone is not None else None
    anchor = case["anchor"]

    for factor in FACTORS:
        recognizer._short_axis_context = factor
        recognized = await recognizer.recognize(
            pixels,
            copy.deepcopy(textlines),
            zero_config,
            _RECOGNIZER_FLAG,
        )
        records = {_geometry_key(line): _line_record(line) for line in recognized}
        factor_observations[f"{factor:.2f}"] = records
        accepted = [
            line
            for line in recognized
            if str(line.text).strip() and float(line.prob) >= PRODUCTION_THRESHOLD
        ]
        merged = await merge(copy.deepcopy(accepted), width, height)
        target_lines = (
            [line for line in recognized if _center_in_zone(line, scaled_zone)]
            if scaled_zone is not None
            else []
        )
        target_accepted = [
            line
            for line in target_lines
            if str(line.text).strip() and float(line.prob) >= PRODUCTION_THRESHOLD
        ]
        target_text = "".join(str(line.text) for line in target_accepted)
        factor_summaries[f"{factor:.2f}"] = {
            "accepted_line_count": len(accepted),
            "merged_region_count": len(merged),
            "target_detector_line_count": len(target_lines),
            "target_accepted_line_count": len(target_accepted),
            "target_anchor_present": bool(anchor and anchor in target_text),
            "target_output_lengths": [len(str(line.text)) for line in target_accepted],
        }

    per_geometry: dict[str, dict[str, Any]] = {}
    for line in textlines:
        key = _geometry_key(line)
        variants = [records[key] for records in factor_observations.values() if key in records]
        texts = {str(item["text"]).strip() for item in variants if str(item["text"]).strip()}
        survival = {bool(item["survives_production_threshold"]) for item in variants}
        lengths = {int(item["text_length"]) for item in variants}
        per_geometry[key] = {
            "xyxy": [int(value) for value in line.xyxy],
            "direction": str(line.direction),
            "distinct_nonempty_texts": len(texts),
            "production_survival_changes": len(survival) > 1,
            "length_changes": len(lengths) > 1,
            "unstable": len(texts) > 1 or len(survival) > 1 or len(lengths) > 1,
        }

    fallback_lines: list[Any]
    if scaled_zone is not None:
        fallback_lines = [line for line in textlines if _center_in_zone(line, scaled_zone)]
    else:
        fallback_lines = _choose_pressure_fallbacks(textlines, factor_observations)

    fallback_crops: list[dict[str, Any]] = []
    for index, line in enumerate(fallback_lines):
        key = _geometry_key(line)
        crop_path = _save_crop(
            str(case["label"]),
            f"line-{index:02d}",
            _source_crop(pixels, line),
        )
        fallback_crops.append(
            {
                "id": f"{case['label']}:line:{index}",
                "case": case["label"],
                "kind": "line",
                "geometry_key": key,
                "path": crop_path,
                "primary_variants": {
                    factor: records.get(key) for factor, records in factor_observations.items()
                },
            }
        )

    if scaled_zone is not None and fallback_lines:
        crop_path = _save_crop(
            str(case["label"]),
            "block",
            _block_crop(pixels, fallback_lines),
        )
        fallback_crops.append(
            {
                "id": f"{case['label']}:block",
                "case": case["label"],
                "kind": "block",
                "path": crop_path,
                "anchor": anchor,
                "detector_line_count": len(fallback_lines),
            }
        )

    unstable_count = sum(1 for item in per_geometry.values() if item["unstable"])
    print(
        "OCR54_RESEARCH "
        f"case={case['label']} detector_count={len(textlines)} "
        f"unstable_count={unstable_count} fallback_crop_count={len(fallback_crops)}"
    )
    return (
        {
            "label": case["label"],
            "file": case["file"],
            "scale": case["scale"],
            "kind": case["kind"],
            "dimensions": {"width": width, "height": height},
            "target_zone": list(scaled_zone) if scaled_zone is not None else None,
            "anchor": anchor,
            "detector_count": len(textlines),
            "factor_summaries": factor_summaries,
            "factor_observations": factor_observations,
            "per_geometry": per_geometry,
            "unstable_count": unstable_count,
        },
        fallback_crops,
    )


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    engine = MangaImageTranslatorEngine(model_cache=MODEL_CACHE, device="cpu")
    detector, recognizer, merge, manifest = await engine._ensure_loaded()

    payload: dict[str, Any] = {
        "research_contract": "ocr54-adaptive-recognition-v1",
        "production_threshold": PRODUCTION_THRESHOLD,
        "factors": list(FACTORS),
        "model_manifest_version": manifest.version,
        "cases": {},
        "fallback_crops": [],
    }
    instability_by_case: dict[str, int] = defaultdict(int)
    for case in CASES:
        result, fallback_crops = await _run_case(engine, detector, recognizer, merge, case)
        payload["cases"][str(case["label"])] = result
        payload["fallback_crops"].extend(fallback_crops)
        instability_by_case[str(case["label"])] = int(result["unstable_count"])

    (OUT / "research-matrix.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT / "summary.json").write_text(
        json.dumps(
            {
                "case_count": len(CASES),
                "fallback_crop_count": len(payload["fallback_crops"]),
                "instability_by_case": instability_by_case,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        "OCR54_RESEARCH_COMPLETE "
        f"cases={len(CASES)} fallback_crops={len(payload['fallback_crops'])}"
    )


if __name__ == "__main__":
    asyncio.run(main())
