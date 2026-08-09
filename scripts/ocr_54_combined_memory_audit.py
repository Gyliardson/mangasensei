"""Measure current OCR plus direct PP-OCRv6 memory under a conservative secondary gate."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import resource
import time
import unicodedata
from pathlib import Path
from typing import Any

from PIL import Image

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import MangaImageTranslatorEngine
from mangasensei.ocr.contracts import OcrImage

MODEL_ID = "PaddlePaddle/PP-OCRv6_medium_rec_safetensors"
PRIMARY_CONFIDENCE_GATE = 0.85
PRIMARY_COVERAGE_GATE = 0.60
WARM_PAGE_LABEL = "black_jack_v01_pdf171"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _semantic_core(text: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFKC", text)
        if not char.isspace()
        and not unicodedata.category(char).startswith(("P", "Z"))
        and char not in {"=", "!", "?", "…", "‼"}
    )


def _needs_secondary(line: dict[str, Any]) -> tuple[bool, float]:
    primary = line.get("primary_48px")
    if not isinstance(primary, dict) or not str(primary.get("text", "")).strip():
        return True, 0.0
    confidence = float(primary.get("confidence", 0.0))
    x1, y1, x2, y2 = (float(value) for value in line["xyxy"])
    font_size = max(float(line["font_size"]), 1.0)
    slot_count = max(x2 - x1, y2 - y1) / font_size
    coverage = len(_semantic_core(str(primary.get("text", "")))) / max(slot_count, 1e-9)
    return confidence < PRIMARY_CONFIDENCE_GATE or coverage < PRIMARY_COVERAGE_GATE, coverage


def _reference_map(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise TypeError("reference observations must be a list")
    return {str(item["id"]): item for item in observations}


def _result_fields(result: Any) -> tuple[str, float]:
    if isinstance(result, dict):
        return str(result.get("rec_text", result.get("text", ""))), float(
            result.get("rec_score", result.get("score", 0.0))
        )
    return str(getattr(result, "rec_text", getattr(result, "text", ""))), float(
        getattr(result, "rec_score", getattr(result, "score", 0.0))
    )


def _peak_rss_mb() -> float:
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


async def _run() -> None:
    args = _parse_args()
    import psutil
    import torch
    from transformers import AutoImageProcessor, AutoModelForTextRecognition

    prepared = args.prepared.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((prepared / "corpus.json").read_text(encoding="utf-8"))
    reference = _reference_map(args.reference.resolve())
    process = psutil.Process(os.getpid())

    line_map: dict[tuple[str, int], dict[str, Any]] = {}
    page_map: dict[str, dict[str, Any]] = {}
    for page in corpus["pages"]:
        label = str(page["label"])
        page_map[label] = page
        for line in page["lines"]:
            line_map[(label, int(line["index"]))] = {**line, "page": label}

    rss_start_mb = process.memory_info().rss / (1024 * 1024)
    primary_engine = MangaImageTranslatorEngine(model_cache=args.model_cache, device="cpu")
    primary_load_started = time.perf_counter()
    await primary_engine._ensure_loaded()
    primary_load_seconds = time.perf_counter() - primary_load_started
    rss_primary_loaded_mb = process.memory_info().rss / (1024 * 1024)

    warm_page = page_map[WARM_PAGE_LABEL]
    fixture_root = (
        Path(__file__).parents[1]
        / "tests"
        / "fixtures"
        / "ocr"
        / "real_manga"
        / "black_jack"
    )
    warm_path = fixture_root / str(warm_page["relative_path"])
    warm_bytes = warm_path.read_bytes()
    with Image.open(warm_path) as warm_image:
        dimensions = PageDimensions(width=warm_image.width, height=warm_image.height)
    primary_infer_started = time.perf_counter()
    primary_result = await primary_engine.analyze(
        OcrImage(
            content=warm_bytes,
            sha256=hashlib.sha256(warm_bytes).hexdigest(),
            media_type="image/jpeg",
            dimensions=dimensions,
        )
    )
    primary_infer_seconds = time.perf_counter() - primary_infer_started
    rss_primary_warm_mb = process.memory_info().rss / (1024 * 1024)

    secondary_load_started = time.perf_counter()
    processor = AutoImageProcessor.from_pretrained(MODEL_ID)
    secondary = AutoModelForTextRecognition.from_pretrained(MODEL_ID)
    secondary.eval()
    secondary_load_seconds = time.perf_counter() - secondary_load_started
    rss_both_loaded_mb = process.memory_info().rss / (1024 * 1024)

    selected: list[dict[str, Any]] = []
    for line in line_map.values():
        needs_secondary, coverage = _needs_secondary(line)
        if needs_secondary:
            selected.append({**line, "primary_coverage": coverage})

    observations: list[dict[str, Any]] = []
    mismatches: list[str] = []
    secondary_infer_seconds = 0.0
    max_rss_sample: dict[str, Any] | None = None
    for line in selected:
        view = "context" if line["direction"] == "v" else "tight"
        input_id = f"{line['page']}--line-{int(line['index']):03d}--{view}"
        image_path = prepared / "crops" / f"{input_id}.png"
        with Image.open(image_path) as source:
            image = source.convert("RGB")
        inputs = processor(images=[image], return_tensors="pt")
        started = time.perf_counter()
        with torch.inference_mode():
            outputs = secondary(**inputs)
            results = processor.post_process_text_recognition(outputs)
        elapsed = time.perf_counter() - started
        secondary_infer_seconds += elapsed
        if len(results) != 1:
            raise RuntimeError(f"secondary recognizer returned {len(results)} results: {input_id}")
        text, confidence = _result_fields(results[0])
        expected = reference.get(input_id)
        if expected is None:
            raise RuntimeError(f"missing PP-OCRv6 reference observation: {input_id}")
        expected_text = str(expected.get("text", ""))
        expected_confidence = float(expected.get("confidence", 0.0))
        if text != expected_text or confidence != expected_confidence:
            mismatches.append(input_id)
        sample = {
            "id": input_id,
            "image_size": list(image.size),
            "input_shape": list(inputs["pixel_values"].shape),
            "seconds": elapsed,
            "rss_mb": process.memory_info().rss / (1024 * 1024),
            "peak_rss_mb": _peak_rss_mb(),
        }
        if max_rss_sample is None or sample["rss_mb"] > max_rss_sample["rss_mb"]:
            max_rss_sample = sample
        observations.append(
            {
                "id": input_id,
                "primary_confidence": (
                    float(line["primary_48px"]["confidence"])
                    if isinstance(line.get("primary_48px"), dict)
                    else 0.0
                ),
                "primary_coverage": line["primary_coverage"],
                "secondary_confidence": confidence,
                "seconds": elapsed,
            }
        )
        del outputs, results, inputs
        image.close()

    payload = {
        "schema_version": 1,
        "implementation_head": corpus.get("implementation_head"),
        "model_id": MODEL_ID,
        "model_commit_hash": getattr(secondary.config, "_commit_hash", None),
        "primary_confidence_gate": PRIMARY_CONFIDENCE_GATE,
        "primary_coverage_gate": PRIMARY_COVERAGE_GATE,
        "corpus_line_count": len(line_map),
        "secondary_selected_count": len(selected),
        "secondary_selected_fraction": len(selected) / max(len(line_map), 1),
        "reference_mismatch_count": len(mismatches),
        "reference_mismatch_ids": mismatches,
        "primary_warm_page": WARM_PAGE_LABEL,
        "primary_warm_region_count": len(primary_result.regions),
        "rss_start_mb": rss_start_mb,
        "rss_primary_loaded_mb": rss_primary_loaded_mb,
        "rss_primary_warm_mb": rss_primary_warm_mb,
        "rss_both_loaded_mb": rss_both_loaded_mb,
        "final_rss_mb": process.memory_info().rss / (1024 * 1024),
        "peak_rss_mb": _peak_rss_mb(),
        "max_rss_sample": max_rss_sample,
        "primary_load_seconds": primary_load_seconds,
        "primary_infer_seconds": primary_infer_seconds,
        "secondary_load_seconds": secondary_load_seconds,
        "secondary_infer_seconds": secondary_infer_seconds,
        "secondary_seconds_per_selected_line": (
            secondary_infer_seconds / len(selected) if selected else 0.0
        ),
        "observations": observations,
    }
    (output / "combined-memory.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_COMBINED_MEMORY "
        f"lines={len(line_map)} selected={len(selected)} mismatches={len(mismatches)} "
        f"rss_primary_warm_mb={rss_primary_warm_mb:.1f} "
        f"rss_both_loaded_mb={rss_both_loaded_mb:.1f} peak_rss_mb={payload['peak_rss_mb']:.1f} "
        f"secondary_seconds={secondary_infer_seconds:.3f}"
    )
    if len(selected) != 41:
        raise AssertionError(
            f"secondary gate selected {len(selected)} lines instead of reviewed 41"
        )
    if mismatches:
        raise AssertionError(
            f"direct secondary differed from reference on {len(mismatches)} lines"
        )


if __name__ == "__main__":
    asyncio.run(_run())
