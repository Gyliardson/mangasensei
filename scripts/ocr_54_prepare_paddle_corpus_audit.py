"""Prepare every licensed detector line for a 48px-vs-Paddle OCR audit.

Investigation only. Recognized licensed text is written to short-lived Actions artifacts, not
ordinary application logs.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
)
from mangasensei.ocr.adapter.recognizer_48px import _copy_quadrilateral

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


def _recognized_by_geometry(lines: list[Any]) -> dict[tuple[tuple[int, int], ...], dict[str, Any]]:
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
    Image.fromarray(pixels.astype(np.uint8), mode="RGB").save(
        path,
        format="PNG",
        optimize=True,
    )


def _annotate_source(
    image: Image.Image,
    textlines: list[Any],
    output: Path,
) -> None:
    rendered = image.convert("RGB")
    draw = ImageDraw.Draw(rendered)
    line_width = max(2, round(max(rendered.size) / 700))
    for index, line in enumerate(textlines):
        x1, y1, x2, y2 = (int(value) for value in line.xyxy)
        draw.rectangle((x1, y1, x2, y2), outline=(210, 25, 55), width=line_width)
        label_x = max(0, min(x1, rendered.width - 28))
        label_y = max(0, y1 - 16)
        draw.rectangle((label_x, label_y, label_x + 28, label_y + 16), fill=(255, 255, 255))
        draw.text((label_x + 2, label_y + 1), str(index), fill=(0, 0, 0))
    output.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(output, format="JPEG", quality=92)


def _fixture_paths() -> list[str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        raise TypeError("licensed OCR fixture manifest must contain a fixture list")
    paths: list[str] = []
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not isinstance(fixture.get("file"), str):
            raise TypeError("licensed OCR fixture entry must contain a string file path")
        paths.append(fixture["file"])
    return paths


async def _run() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, recognizer, _, manifest = await engine._ensure_loaded()

    payload: dict[str, Any] = {
        "schema_version": 1,
        "source_head": "59f12956582a5a3af482339a806585b8650cbd8e",
        "upstream_commit": manifest.upstream_commit,
        "pages": [],
        "inputs": [],
    }
    total_detector_lines = 0
    total_48px_lines = 0
    detector_seconds_total = 0.0
    recognizer_seconds_total = 0.0

    for relative_path in _fixture_paths():
        source_path = FIXTURE_ROOT / relative_path
        source_bytes = source_path.read_bytes()
        with Image.open(io.BytesIO(source_bytes)) as source:
            image = source.convert("RGB")
        pixels = np.asarray(image)

        detector_started = time.perf_counter()
        textlines, _, _ = await detector.detect(
            pixels,
            engine._detection_size,
            engine._text_threshold,
            engine._box_threshold,
            engine._unclip_ratio,
            *_DETECTOR_FLAGS,
        )
        detector_seconds = time.perf_counter() - detector_started
        detector_seconds_total += detector_seconds

        recognition_started = time.perf_counter()
        recognized = await recognizer.recognize(
            pixels,
            copy.deepcopy(textlines),
            engine._ocr_config,
            _RECOGNIZER_FLAG,
        )
        recognition_seconds = time.perf_counter() - recognition_started
        recognizer_seconds_total += recognition_seconds
        recognition = _recognized_by_geometry(recognized)

        page_label = Path(relative_path).stem
        annotation_rel = Path("annotated") / f"{page_label}.jpg"
        _annotate_source(image, textlines, output / annotation_rel)

        page_entry: dict[str, Any] = {
            "relative_path": relative_path,
            "label": page_label,
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "image_size": [image.width, image.height],
            "detector_seconds": detector_seconds,
            "recognizer_48px_seconds": recognition_seconds,
            "detector_candidate_count": len(textlines),
            "recognized_48px_count": len(recognition),
            "annotation": annotation_rel.as_posix(),
            "lines": [],
        }

        for index, line in enumerate(textlines):
            direction = str(line.direction)
            crop_quad = _copy_quadrilateral(line)
            crop = crop_quad.get_transformed_region(pixels, direction, 48)
            crop_rel = Path("crops") / f"{page_label}--line-{index:03d}.png"
            _save_rgb(output / crop_rel, crop)
            key = _geometry_key(line)
            result_48px = recognition.get(key)
            page_entry["lines"].append(
                {
                    "index": index,
                    "geometry": [list(point) for point in key],
                    "xyxy": [int(value) for value in line.xyxy],
                    "direction": direction,
                    "detector_probability": float(line.prob),
                    "font_size": float(line.font_size),
                    "ocr_48px": result_48px,
                    "crop": crop_rel.as_posix(),
                }
            )
            payload["inputs"].append(
                {
                    "id": f"{page_label}--line-{index:03d}",
                    "page": page_label,
                    "relative_path": relative_path,
                    "line_index": index,
                    "path": crop_rel.as_posix(),
                }
            )

        payload["pages"].append(page_entry)
        total_detector_lines += len(textlines)
        total_48px_lines += len(recognition)
        print(
            "OCR_PADDLE_CORPUS_PREP "
            f"fixture={relative_path} detector_candidates={len(textlines)} "
            f"recognized_48px={len(recognition)}"
        )

    payload["totals"] = {
        "pages": len(payload["pages"]),
        "detector_lines": total_detector_lines,
        "recognized_48px_lines": total_48px_lines,
        "detector_seconds": detector_seconds_total,
        "recognizer_48px_seconds": recognizer_seconds_total,
    }
    (output / "corpus.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(_run())
