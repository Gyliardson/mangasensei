"""Run one external OCR recognizer over prepared licensed benchmark crops."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import resource
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image

MANGA_OCR_REPOSITORY = "kha-white/manga-ocr-base"
MANGA_OCR_REVISION = "aa6573bd10b0d446cbf622e29c3e084914df9741"
MANGA_OCR_MODEL_FILE = "pytorch_model.bin"
MANGA_OCR_MODEL_SIZE = 444_135_475
MANGA_OCR_MODEL_SHA256 = "c63e0bb5b3ff798c5991de18a8e0956c7ee6d1563aca6729029815eda6f5c2eb"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recognizer", choices=("manga-ocr", "paddle-v6"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _peak_rss_mb() -> float:
    # Linux ru_maxrss is KiB.
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _run_manga_ocr(root: Path, inputs: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    from huggingface_hub import snapshot_download
    from manga_ocr import MangaOcr

    model_root = output / "manga-ocr-model"
    model_root.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=MANGA_OCR_REPOSITORY,
        revision=MANGA_OCR_REVISION,
        local_dir=model_root,
    )
    model_file = model_root / MANGA_OCR_MODEL_FILE
    if not model_file.is_file():
        raise FileNotFoundError(f"pinned MangaOCR model file is missing: {model_file}")
    actual_size = model_file.stat().st_size
    actual_sha256 = _sha256(model_file)
    if actual_size != MANGA_OCR_MODEL_SIZE or actual_sha256 != MANGA_OCR_MODEL_SHA256:
        raise RuntimeError(
            "pinned MangaOCR model integrity check failed: "
            f"size={actual_size} sha256={actual_sha256}"
        )

    load_started = time.perf_counter()
    recognizer = MangaOcr(pretrained_model_name_or_path=str(model_root), force_cpu=True)
    load_seconds = time.perf_counter() - load_started

    observations: list[dict[str, Any]] = []
    for item in inputs:
        image_path = root / str(item["path"])
        started = time.perf_counter()
        with Image.open(image_path) as image:
            text = recognizer(image.convert("RGB"))
        elapsed = time.perf_counter() - started
        observations.append(
            {
                "id": item["id"],
                "case": item["case"],
                "kind": item["kind"],
                "text": str(text),
                "seconds": elapsed,
                "peak_rss_mb": _peak_rss_mb(),
            }
        )
        print(f"OCR_ENSEMBLE_RUN recognizer=manga-ocr id={item['id']} seconds={elapsed:.3f}")

    return {
        "recognizer": "manga-ocr",
        "package_version": _package_version("manga-ocr"),
        "transformers_version": _package_version("transformers"),
        "model_repository": MANGA_OCR_REPOSITORY,
        "model_revision": MANGA_OCR_REVISION,
        "model_file": MANGA_OCR_MODEL_FILE,
        "model_size": actual_size,
        "model_sha256": actual_sha256,
        "load_seconds": load_seconds,
        "peak_rss_mb": _peak_rss_mb(),
        "observations": observations,
    }


def _paddle_result_payload(result: Any) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mangasensei-paddle-result-") as temp_dir:
        output_path = Path(temp_dir) / "result.json"
        result.save_to_json(save_path=str(output_path))
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("PaddleOCR recognition result JSON is not an object")
    nested = payload.get("res", payload)
    if not isinstance(nested, dict):
        raise TypeError("PaddleOCR recognition result 'res' is not an object")
    return nested


def _run_paddle_v6(root: Path, inputs: list[dict[str, Any]], output: Path) -> dict[str, Any]:
    from paddleocr import TextRecognition

    load_started = time.perf_counter()
    recognizer = TextRecognition(
        model_name="PP-OCRv6_medium_rec",
        engine="transformers",
        device="cpu",
    )
    load_seconds = time.perf_counter() - load_started

    observations: list[dict[str, Any]] = []
    for item in inputs:
        if item["kind"] == "block":
            continue
        image_path = root / str(item["path"])
        started = time.perf_counter()
        results = list(recognizer.predict(input=str(image_path), batch_size=1))
        elapsed = time.perf_counter() - started
        if len(results) != 1:
            raise RuntimeError(
                f"PP-OCRv6 returned {len(results)} results for one line crop: {item['id']}"
            )
        result_payload = _paddle_result_payload(results[0])
        observations.append(
            {
                "id": item["id"],
                "case": item["case"],
                "kind": item["kind"],
                "text": str(result_payload.get("rec_text", "")),
                "confidence": float(result_payload.get("rec_score", 0.0)),
                "seconds": elapsed,
                "peak_rss_mb": _peak_rss_mb(),
            }
        )
        print(f"OCR_ENSEMBLE_RUN recognizer=paddle-v6 id={item['id']} seconds={elapsed:.3f}")

    cache_roots = [
        Path(os.environ.get("HF_HOME", "")),
        Path(os.environ.get("XDG_CACHE_HOME", "")),
        Path.home() / ".paddlex",
        Path.home() / ".cache" / "huggingface",
    ]
    downloaded_files: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for cache_root in cache_roots:
        if not str(cache_root) or not cache_root.exists():
            continue
        for path in cache_root.rglob("*"):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            if path.stat().st_size < 1024:
                continue
            downloaded_files.append(
                {
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )

    return {
        "recognizer": "paddle-v6",
        "package_version": _package_version("paddleocr"),
        "transformers_version": _package_version("transformers"),
        "model_name": "PP-OCRv6_medium_rec",
        "engine": "transformers",
        "load_seconds": load_seconds,
        "peak_rss_mb": _peak_rss_mb(),
        "downloaded_files": downloaded_files,
        "observations": observations,
    }


def main() -> None:
    args = _parse_args()
    root = args.input.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    benchmark = json.loads((root / "benchmark.json").read_text(encoding="utf-8"))
    inputs = benchmark.get("inputs")
    if not isinstance(inputs, list):
        raise TypeError("benchmark inputs must be a list")

    if args.recognizer == "manga-ocr":
        payload = _run_manga_ocr(root, inputs, output)
    else:
        payload = _run_paddle_v6(root, inputs, output)

    (output / f"{args.recognizer}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
