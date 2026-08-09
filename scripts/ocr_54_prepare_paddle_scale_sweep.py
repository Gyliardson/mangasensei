"""Prepare reviewed line crops to tune PP-OCRv6 rectification without changing DBNet geometry."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
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
HEIGHTS = (48, 64, 72, 96)
CONTEXT_FACTORS = (1.0, RECOGNITION_SHORT_AXIS_CONTEXT)


@dataclass(frozen=True, slots=True)
class ReviewedLine:
    page: str
    line_index: int
    expected: str
    role: str


CASES = (
    ReviewedLine("black_jack_v01_pdf007", 3, "じゃあ僕の", "48px-strength-small-kana"),
    ReviewedLine("black_jack_v01_pdf009", 3, "術野を広げろ", "paddle-strength-completeness"),
    ReviewedLine("black_jack_v01_pdf009", 11, "含まれていない‼", "punctuation"),
    ReviewedLine("black_jack_v01_pdf021", 7, "", "negative-keypad"),
    ReviewedLine("black_jack_v01_pdf066", 6, "だと思って", "48px-strength-small-tsu"),
    ReviewedLine("black_jack_v01_pdf066", 13, "20整形", "paddle-strength-signage"),
    ReviewedLine("black_jack_v01_pdf066", 14, "1内科/麻酔科", "signage-japanese-variant"),
    ReviewedLine("black_jack_v01_pdf066", 16, "Information Desk", "environmental-english"),
    ReviewedLine("black_jack_v01_pdf066", 25, "医者ってやつは", "48px-strength-small-tsu"),
    ReviewedLine("black_jack_v01_pdf073", 11, "切り口です", "paddle-strength-confusable"),
    ReviewedLine("black_jack_v01_pdf090", 7, "じいちゃんもきっと", "48px-strength-small-kana"),
    ReviewedLine("black_jack_v01_pdf123", 14, "なかったとしても", "48px-strength-small-tsu"),
    ReviewedLine("black_jack_v01_pdf145", 0, "春日部一郎博士", "paddle-strength-confusable"),
    ReviewedLine("black_jack_v01_pdf145", 5, "第十一代主任教授", "paddle-strength-recall"),
    ReviewedLine(
        "black_jack_v01_pdf171",
        5,
        "※ステント＝心臓の血管の狭くなった部分をふくらませて血流をよくしたあと、再びその部分が",
        "48px-strength-long-footnote",
    ),
    ReviewedLine("black_jack_v01_pdf194", 2, "きったはったの", "48px-strength-small-tsu"),
    ReviewedLine("black_jack_v01_pdf201", 0, "", "negative-pattern"),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, default=Path("var/models"))
    return parser.parse_args()


def _fixture_path(page: str) -> Path:
    return FIXTURE_ROOT / "v01" / f"{page}.jpg"


def _save_rgb(path: Path, pixels: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(pixels.astype(np.uint8), mode="RGB").save(path, format="PNG", optimize=True)


async def _run() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    detector, _, _, _ = await engine._ensure_loaded()

    cases_by_page: dict[str, list[ReviewedLine]] = {}
    for case in CASES:
        cases_by_page.setdefault(case.page, []).append(case)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "source_head": "59f12956582a5a3af482339a806585b8650cbd8e",
        "heights": list(HEIGHTS),
        "context_factors": list(CONTEXT_FACTORS),
        "cases": [],
        "inputs": [],
    }

    for page, cases in cases_by_page.items():
        source_path = _fixture_path(page)
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

        for case in cases:
            if case.line_index >= len(textlines):
                raise AssertionError(
                    f"reviewed detector line disappeared: {case.page}#{case.line_index}"
                )
            line = textlines[case.line_index]
            case_entry = {
                "page": case.page,
                "line_index": case.line_index,
                "expected": case.expected,
                "role": case.role,
                "direction": str(line.direction),
                "xyxy": [int(value) for value in line.xyxy],
                "variants": [],
            }
            for context_factor in CONTEXT_FACTORS:
                if context_factor == 1.0:
                    source_quad = _copy_quadrilateral(line)
                    context_label = "tight"
                else:
                    source_quad = _expand_short_axis(
                        line,
                        factor=context_factor,
                        image_width=image.width,
                        image_height=image.height,
                    )
                    context_label = "context"
                for textheight in HEIGHTS:
                    crop = source_quad.get_transformed_region(
                        pixels,
                        str(line.direction),
                        textheight,
                    )
                    variant_id = (
                        f"{case.page}--line-{case.line_index:03d}--{context_label}--h{textheight}"
                    )
                    crop_rel = Path("crops") / f"{variant_id}.png"
                    _save_rgb(output / crop_rel, crop)
                    variant = {
                        "id": variant_id,
                        "context": context_label,
                        "context_factor": context_factor,
                        "textheight": textheight,
                        "crop": crop_rel.as_posix(),
                    }
                    case_entry["variants"].append(variant)
                    payload["inputs"].append(
                        {
                            "id": variant_id,
                            "page": case.page,
                            "relative_path": str(source_path.relative_to(FIXTURE_ROOT)),
                            "line_index": case.line_index,
                            "path": crop_rel.as_posix(),
                        }
                    )
            payload["cases"].append(case_entry)
        print(f"OCR_PADDLE_SCALE_PREP page={page} reviewed_lines={len(cases)}")

    (output / "corpus.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(_run())
