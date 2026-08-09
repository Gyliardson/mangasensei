from __future__ import annotations

import asyncio
import copy
import json
import os
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
from mangasensei.ocr.adapter.recognizer_48px import (
    _copy_quadrilateral,
    _expand_short_axis,
)
from mangasensei.ocr.vendor.manga_image_translator.manga_translator.ocr.model_48px_ctc import (
    Model48pxCTCOCR,
)

FIXTURE_ROOT = Path("tests/fixtures/ocr/real_manga/black_jack/v01")
OUT = Path(os.environ.get("MANGASENSEI_OCR_CTC_DIR", "var/ocr-54-ctc"))
MODEL_CACHE = Path(os.environ.get("MANGASENSEI_MODEL_CACHE", "var/models"))
CTC_MODEL_CACHE = Path(os.environ.get("MANGASENSEI_CTC_MODEL_CACHE", "var/models-ctc"))
FACTORS = (1.0, 1.16)
PRODUCTION_THRESHOLD = 0.2

CASES = (
    {
        "label": "page9-scale-090",
        "file": "black_jack_v01_pdf009.jpg",
        "scale": 0.9,
        "target_zone": (130, 285, 455, 745),
        "anchor": "国家試験に合格しなければいけない",
    },
    {
        "label": "page171",
        "file": "black_jack_v01_pdf171.jpg",
        "scale": 1.0,
        "target_zone": (70, 220, 650, 1800),
        "anchor": "※ステント＝心臓の血管",
    },
    {
        "label": "page73",
        "file": "black_jack_v01_pdf073.jpg",
        "scale": 1.0,
        "target_zone": (500, 620, 250, 420),
        "anchor": "うむ",
    },
    {
        "label": "page90",
        "file": "black_jack_v01_pdf090.jpg",
        "scale": 1.0,
        "target_zone": (1030, 1210, 160, 480),
        "anchor": "はい",
    },
    {
        "label": "page21-pressure",
        "file": "black_jack_v01_pdf021.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
    },
    {
        "label": "page41-pressure",
        "file": "black_jack_v01_pdf041.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
    },
    {
        "label": "page145-pressure",
        "file": "black_jack_v01_pdf145.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
    },
    {
        "label": "page201-pressure",
        "file": "black_jack_v01_pdf201.jpg",
        "scale": 1.0,
        "target_zone": None,
        "anchor": None,
    },
)


def _geometry_key(line: Any) -> str:
    return ";".join(f"{int(point[0])},{int(point[1])}" for point in np.asarray(line.pts))


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


def _scaled_zone(case: dict[str, Any]) -> tuple[int, int, int, int] | None:
    zone = case["target_zone"]
    if zone is None:
        return None
    factor = float(case["scale"])
    return tuple(round(value * factor) for value in zone)  # type: ignore[return-value]


def _center_in_zone(line: Any, zone: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = (float(value) for value in line.xyxy)
    cx = (x1 + x2) / 2
    cy = (y1 + y2) / 2
    min_x, max_x, min_y, max_y = zone
    return min_x <= cx <= max_x and min_y <= cy <= max_y


def _recognition_copy(line: Any, factor: float, width: int, height: int) -> Any:
    copied = _copy_quadrilateral(line)
    if factor == 1.0:
        return copied
    return _expand_short_axis(
        copied,
        factor=factor,
        image_width=width,
        image_height=height,
    )


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


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    engine = MangaImageTranslatorEngine(model_cache=MODEL_CACHE, device="cpu")
    detector, ar_recognizer, _, manifest = await engine._ensure_loaded()

    Model48pxCTCOCR._MODEL_DIR = str(CTC_MODEL_CACHE)
    ctc = Model48pxCTCOCR()
    await ctc.download()
    await ctc.load("cpu")

    zero_config = type(engine._ocr_config)(prob=0.0, ignore_bubble=0)
    payload: dict[str, Any] = {
        "research_contract": "ocr54-ctc-complement-v1",
        "production_threshold": PRODUCTION_THRESHOLD,
        "factors": list(FACTORS),
        "product_model_manifest_version": manifest.version,
        "cases": {},
    }

    for case in CASES:
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
        zone = _scaled_zone(case)
        case_result: dict[str, Any] = {
            "detector_count": len(textlines),
            "target_zone": list(zone) if zone is not None else None,
            "anchor": case["anchor"],
            "variants": {},
        }

        for factor in FACTORS:
            candidates = [
                _recognition_copy(line, factor, width, height) for line in textlines
            ]
            recognized = await ctc.recognize(
                pixels,
                candidates,
                zero_config,
                _RECOGNIZER_FLAG,
            )
            records = [_line_record(line) for line in recognized]
            target = (
                [record for line, record in zip(recognized, records) if _center_in_zone(line, zone)]
                if zone is not None
                else []
            )
            target_text = "".join(str(item["text"]) for item in target)
            case_result["variants"][f"ctc-{factor:.2f}"] = {
                "recognized": records,
                "accepted_count": sum(
                    1 for item in records if item["survives_production_threshold"]
                ),
                "target_count": len(target),
                "target_accepted_count": sum(
                    1 for item in target if item["survives_production_threshold"]
                ),
                "target_anchor_present": bool(
                    case["anchor"] and str(case["anchor"]) in target_text
                ),
                "target_lengths": [int(item["text_length"]) for item in target],
            }

        ar_recognizer._short_axis_context = 1.16
        ar_lines = await ar_recognizer.recognize(
            pixels,
            copy.deepcopy(textlines),
            zero_config,
            _RECOGNIZER_FLAG,
        )
        ar_records = [_line_record(line) for line in ar_lines]
        ar_target = (
            [record for line, record in zip(ar_lines, ar_records) if _center_in_zone(line, zone)]
            if zone is not None
            else []
        )
        case_result["variants"]["ar-1.16"] = {
            "recognized": ar_records,
            "accepted_count": sum(
                1 for item in ar_records if item["survives_production_threshold"]
            ),
            "target_count": len(ar_target),
            "target_accepted_count": sum(
                1 for item in ar_target if item["survives_production_threshold"]
            ),
            "target_anchor_present": bool(
                case["anchor"]
                and str(case["anchor"])
                in "".join(str(item["text"]) for item in ar_target)
            ),
            "target_lengths": [int(item["text_length"]) for item in ar_target],
        }
        payload["cases"][str(case["label"])] = case_result
        print(
            "OCR54_CTC "
            f"case={case['label']} detector_count={len(textlines)} "
            + " ".join(
                f"{name}_accepted={variant['accepted_count']} "
                f"{name}_target={variant['target_accepted_count']} "
                f"{name}_anchor={variant['target_anchor_present']}"
                for name, variant in case_result["variants"].items()
            )
        )

    (OUT / "ctc-results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"OCR54_CTC_COMPLETE cases={len(CASES)}")


if __name__ == "__main__":
    asyncio.run(main())
