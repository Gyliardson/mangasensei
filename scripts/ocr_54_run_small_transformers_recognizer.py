"""Run the official small PP-OCRv6 recognizer over prepared detector crops."""

from __future__ import annotations

import argparse
import json
import os
import resource
import time
from pathlib import Path
from typing import Any

from PIL import Image

MODEL_ID = "PaddlePaddle/PP-OCRv6_small_rec_safetensors"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=1)
    return parser.parse_args()


def _result_fields(result: Any) -> tuple[str, float]:
    if isinstance(result, dict):
        return str(result.get("rec_text", result.get("text", ""))), float(
            result.get("rec_score", result.get("score", 0.0))
        )
    return str(getattr(result, "rec_text", getattr(result, "text", ""))), float(
        getattr(result, "rec_score", getattr(result, "score", 0.0))
    )


def main() -> None:
    args = _parse_args()
    import psutil
    import torch
    from transformers import AutoImageProcessor, AutoModelForTextRecognition

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((args.input / "corpus.json").read_text(encoding="utf-8"))
    raw_inputs = corpus.get("inputs")
    if not isinstance(raw_inputs, list):
        raise TypeError("prepared corpus inputs must be a list")

    process = psutil.Process(os.getpid())
    load_started = time.perf_counter()
    processor = AutoImageProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForTextRecognition.from_pretrained(MODEL_ID)
    model.eval()
    load_seconds = time.perf_counter() - load_started
    rss_after_load_mb = process.memory_info().rss / (1024 * 1024)

    observations: list[dict[str, Any]] = []
    infer_seconds = 0.0
    for start in range(0, len(raw_inputs), args.batch_size):
        batch = raw_inputs[start : start + args.batch_size]
        images = [Image.open(args.input / item["path"]).convert("RGB") for item in batch]
        inputs = processor(images=images, return_tensors="pt")
        started = time.perf_counter()
        with torch.inference_mode():
            outputs = model(**inputs)
            results = processor.post_process_text_recognition(outputs)
        infer_seconds += time.perf_counter() - started
        if len(results) != len(batch):
            raise RuntimeError("small PP-OCRv6 result count does not match input batch")
        for item, result in zip(batch, results, strict=True):
            text, confidence = _result_fields(result)
            observations.append(
                {
                    "id": str(item["id"]),
                    "text": text,
                    "confidence": confidence,
                }
            )
        del outputs, results, inputs
        for image in images:
            image.close()

    payload = {
        "schema_version": 1,
        "model_id": MODEL_ID,
        "model_commit_hash": getattr(model.config, "_commit_hash", None),
        "input_count": len(observations),
        "batch_size": args.batch_size,
        "inference_mode": True,
        "load_seconds": load_seconds,
        "inference_seconds": infer_seconds,
        "seconds_per_crop": infer_seconds / max(len(observations), 1),
        "rss_after_load_mb": rss_after_load_mb,
        "final_rss_mb": process.memory_info().rss / (1024 * 1024),
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "observations": observations,
    }
    (output / "small-transformers.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_SMALL_TRANSFORMERS "
        f"inputs={len(observations)} load_seconds={load_seconds:.3f} "
        f"infer_seconds={infer_seconds:.3f} rss_after_load_mb={rss_after_load_mb:.1f} "
        f"peak_rss_mb={payload['peak_rss_mb']:.1f}"
    )


if __name__ == "__main__":
    main()
