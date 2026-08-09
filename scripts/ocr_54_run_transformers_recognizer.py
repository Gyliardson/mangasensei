"""Run PP-OCRv6 directly through Transformers on prepared detector crops."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import time
from pathlib import Path
from typing import Any

from PIL import Image

MODEL_ID = "PaddlePaddle/PP-OCRv6_medium_rec_safetensors"
MODEL_CACHE_NAME = "models--PaddlePaddle--PP-OCRv6_medium_rec_safetensors"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=16)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result_fields(result: Any) -> tuple[str, float]:
    if isinstance(result, dict):
        text = result.get("rec_text", result.get("text", ""))
        score = result.get("rec_score", result.get("score", 0.0))
        return str(text), float(score)
    text = getattr(result, "rec_text", getattr(result, "text", ""))
    score = getattr(result, "rec_score", getattr(result, "score", 0.0))
    return str(text), float(score)


def _reference_map(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise TypeError("reference observations must be a list")
    return {str(item["id"]): item for item in observations}


def _model_files(model: Any, processor: Any) -> list[dict[str, Any]]:
    candidates: set[Path] = set()
    for item in (model, processor):
        commit_hash = getattr(item, "_commit_hash", None)
        name_or_path = getattr(item, "name_or_path", None)
        if isinstance(name_or_path, str):
            path = Path(name_or_path)
            if path.exists():
                candidates.add(path)
        if isinstance(commit_hash, str):
            default_cache = Path.home() / ".cache" / "huggingface"
            cache_root = Path(os.environ.get("HF_HOME", default_cache))
            snapshot = cache_root / "hub" / MODEL_CACHE_NAME / "snapshots" / commit_hash
            if snapshot.exists():
                candidates.add(snapshot)
    files: list[dict[str, Any]] = []
    for root in sorted(candidates):
        if root.is_file():
            paths = [root]
        else:
            paths = sorted(path for path in root.rglob("*") if path.is_file())
        for path in paths:
            resolved = path.resolve()
            if any(entry["path"] == str(resolved) for entry in files):
                continue
            files.append(
                {
                    "path": str(resolved),
                    "size": resolved.stat().st_size,
                    "sha256": _sha256(resolved),
                }
            )
    return files


def main() -> None:
    args = _parse_args()
    from transformers import AutoImageProcessor, AutoModelForTextRecognition

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((args.input / "corpus.json").read_text(encoding="utf-8"))
    raw_inputs = corpus.get("inputs")
    if not isinstance(raw_inputs, list):
        raise TypeError("prepared corpus inputs must be a list")
    reference = _reference_map(args.reference)

    load_started = time.perf_counter()
    processor = AutoImageProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForTextRecognition.from_pretrained(MODEL_ID)
    model.eval()
    load_seconds = time.perf_counter() - load_started

    observations: list[dict[str, Any]] = []
    infer_seconds = 0.0
    for start in range(0, len(raw_inputs), args.batch_size):
        batch = raw_inputs[start : start + args.batch_size]
        images = [Image.open(args.input / item["path"]).convert("RGB") for item in batch]
        inputs = processor(images=images, return_tensors="pt")
        started = time.perf_counter()
        outputs = model(**inputs)
        results = processor.post_process_text_recognition(outputs)
        infer_seconds += time.perf_counter() - started
        if len(results) != len(batch):
            raise RuntimeError("Transformers result count does not match input batch")
        for item, result in zip(batch, results, strict=True):
            text, confidence = _result_fields(result)
            observations.append(
                {
                    "id": str(item["id"]),
                    "text": text,
                    "confidence": confidence,
                }
            )
        for image in images:
            image.close()

    direct = {item["id"]: item for item in observations}
    text_mismatches: list[dict[str, Any]] = []
    confidence_deltas: list[float] = []
    for input_id, reference_item in reference.items():
        item = direct.get(input_id)
        if item is None:
            raise RuntimeError(f"missing direct Transformers observation: {input_id}")
        reference_text = str(reference_item.get("text", ""))
        if item["text"] != reference_text:
            text_mismatches.append(
                {
                    "id": input_id,
                    "paddleocr": reference_text,
                    "transformers": item["text"],
                }
            )
        confidence_deltas.append(
            abs(float(item["confidence"]) - float(reference_item.get("confidence", 0.0)))
        )

    model_files = _model_files(model, processor)
    payload = {
        "schema_version": 1,
        "model_id": MODEL_ID,
        "model_commit_hash": getattr(model.config, "_commit_hash", None),
        "processor_commit_hash": getattr(processor, "_commit_hash", None),
        "input_count": len(observations),
        "load_seconds": load_seconds,
        "inference_seconds": infer_seconds,
        "seconds_per_crop": infer_seconds / max(len(observations), 1),
        "peak_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
        "text_mismatch_count": len(text_mismatches),
        "text_mismatches": text_mismatches,
        "max_confidence_delta": max(confidence_deltas, default=0.0),
        "model_files": model_files,
        "observations": observations,
    }
    (output / "transformers-direct.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_TRANSFORMERS_DIRECT "
        f"inputs={len(observations)} mismatches={len(text_mismatches)} "
        f"load_seconds={load_seconds:.3f} infer_seconds={infer_seconds:.3f} "
        f"peak_rss_mb={payload['peak_rss_mb']:.1f}"
    )
    if text_mismatches:
        raise AssertionError(
            f"direct Transformers output differs from PaddleOCR on {len(text_mismatches)} crops"
        )


if __name__ == "__main__":
    main()
