"""Prepare production 48px plus tight/context secondary crops for router experiments."""

from __future__ import annotations

import argparse
import asyncio
import copy
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
)
from mangasensei.ocr.adapter.recognizer_48px import (
    _copy_quadrilateral,
    _expand_short_axis,
)
from mangasensei.ocr.adapter.recognizer_contract import RECOGNITION_SHORT_AXIS_CONTEXT

FIXTURE_ROOT = (
    Path(__file__).parents[1]
    / "tests"
    / "fixtures"
    / "ocr"
    / "real_manga"
    / "black_jack"
)
MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, default=Path("var/models"))
    return parser.parse_args()


def _geometry_key(line: Any) -> tuple[tuple[int, int], ...]:
    return tuple((int(point[0]), int(point[1])) for point in np.asarray(line.pts))


def _recognized_map(lines: list[Any]) -> dict[tuple[tuple[int, int], ...], dict[str, Any]]:
    return {
        _geometry_key(line): {
            "text": str(line.text),
            "confidence": float(line.prob),
        }
        for line in lines
        if str(line.text).strip()
    }


def _save_rgb(path: Path, pixels: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels.astype(np.uint8), mode="RGB").save(path, format="PNG", optimize=True)


def _fixture_paths() -> list[str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        raise TypeError("licensed OCR manifest fixtures must be a list")
    result: list[str] = []
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not isinstance(fixture.get("file"), str):
            raise TypeError("licensed OCR fixture must contain a string file path")
        result.append(fixture["file"])
    return result


async def _run() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, _, _ = await engine._ensure_loaded()

    payload: dict[str, Any] = {
        "schema_version": 1,
        "implementation_head": "59f12956582a5a3af482339a806585b8650cbd8e",
        "secondary_context_factor": RECOGNITION_SHORT_AXIS_CONTEXT,
        "pages": [],
        "inputs": [],
    }

    for relative_path in _fixture_paths():
        source_path = FIXTURE_ROOT / relative_path
        source_bytes = source_path.read_bytes()
        with Image.open(io.BytesIO(source_bytes)) as source:
            image = source.convert("RGB")
        pixels = np.asarray(image)
        textlines, _, _ = await detector.detect(
            pixels,
            engine._detection_size,
            engine._text_threshold,
            engine._box_threshold,
            engine._unclip_ratio,
            *_DETECTOR_FLAGS,
        )
        primary_lines = await recognizer.recognize(
            pixels,
            copy.deepcopy(textlines),
            engine._ocr_config,
            _RECOGNIZER_FLAG,
        )
        primary = _recognized_map(primary_lines)
        page_label = Path(relative_path).stem
        page_entry: dict[str, Any] = {
            "label": page_label,
            "relative_path": relative_path,
            "lines": [],
        }
        for index, line in enumerate(textlines):
            key = _geometry_key(line)
            direction = str(line.direction)
            tight = _copy_quadrilateral(line)
            context = _expand_short_axis(
                line,
                factor=RECOGNITION_SHORT_AXIS_CONTEXT,
                image_width=image.width,
                image_height=image.height,
            )
            for view, quadrilateral in (("tight", tight), ("context", context)):
                crop = quadrilateral.get_transformed_region(pixels, direction, 48)
                crop_rel = Path("crops") / f"{page_label}--line-{index:03d}--{view}.png"
                _save_rgb(output / crop_rel, crop)
                payload["inputs"].append(
                    {
                        "id": f"{page_label}--line-{index:03d}--{view}",
                        "page": page_label,
                        "relative_path": relative_path,
                        "line_index": index,
                        "view": view,
                        "path": crop_rel.as_posix(),
                    }
                )
            page_entry["lines"].append(
                {
                    "index": index,
                    "xyxy": [int(value) for value in line.xyxy],
                    "direction": direction,
                    "font_size": float(line.font_size),
                    "detector_probability": float(line.prob),
                    "primary_48px": primary.get(key),
                }
            )
        payload["pages"].append(page_entry)
        print(
            "OCR_ROUTER_PREP "
            f"page={page_label} detector_lines={len(textlines)} primary_lines={len(primary)}"
        )

    (output / "corpus.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(_run())
