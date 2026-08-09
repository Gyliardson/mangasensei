"""Run a selected PaddleOCR recognizer over prepared licensed line crops.

Investigation only. Recognized licensed text is written to short-lived Actions artifacts, not
ordinary application logs.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import resource
import tempfile
import time
from pathlib import Path
from typing import Any

DEFAULT_MODEL_NAME = "PP-OCRv6_medium_rec"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--id-regex")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _peak_rss_mb() -> float:
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _result_payload(result: Any) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="mangasensei-paddle-corpus-") as temp_dir:
        output_path = Path(temp_dir) / "result.json"
        result.save_to_json(save_path=str(output_path))
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("PaddleOCR recognition result JSON is not an object")
    nested = payload.get("res", payload)
    if not isinstance(nested, dict):
        raise TypeError("PaddleOCR recognition result 'res' is not an object")
    return nested


def _downloaded_model_files() -> list[dict[str, Any]]:
    roots = [
        Path(os.environ.get("HF_HOME", "")),
        Path(os.environ.get("XDG_CACHE_HOME", "")),
        Path.home() / ".paddlex",
        Path.home() / ".cache" / "huggingface",
    ]
    files: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in roots:
        if not str(root) or not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path in seen or path.stat().st_size < 1024:
                continue
            seen.add(path)
            files.append(
                {
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return files


def main() -> None:
    from paddleocr import TextRecognition

    args = _parse_args()
    root = args.input.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    inputs = corpus.get("inputs")
    if not isinstance(inputs, list):
        raise TypeError("corpus inputs must be a list")
    id_pattern = re.compile(args.id_regex) if args.id_regex else None
    selected_inputs = [
        item
        for item in inputs
        if id_pattern is None or id_pattern.search(str(item.get("id", "")))
    ]
    if not selected_inputs:
        raise ValueError("Paddle benchmark input filter selected no crops")

    load_started = time.perf_counter()
    recognizer = TextRecognition(
        model_name=args.model_name,
        engine="transformers",
        device="cpu",
    )
    load_seconds = time.perf_counter() - load_started

    observations: list[dict[str, Any]] = []
    total_seconds = 0.0
    for item in selected_inputs:
        image_path = root / str(item["path"])
        started = time.perf_counter()
        results = list(recognizer.predict(input=str(image_path), batch_size=1))
        elapsed = time.perf_counter() - started
        total_seconds += elapsed
        if len(results) != 1:
            raise RuntimeError(
                f"PaddleOCR returned {len(results)} results for one crop: {item['id']}"
            )
        result = _result_payload(results[0])
        observations.append(
            {
                "id": item["id"],
                "page": item["page"],
                "relative_path": item["relative_path"],
                "line_index": item["line_index"],
                "text": str(result.get("rec_text", "")),
                "confidence": float(result.get("rec_score", 0.0)),
                "seconds": elapsed,
                "peak_rss_mb": _peak_rss_mb(),
            }
        )
        print(
            "OCR_PADDLE_CORPUS_RUN "
            f"model={args.model_name} page={item['page']} "
            f"line={item['line_index']} seconds={elapsed:.3f}"
        )

    payload = {
        "schema_version": 2,
        "recognizer": "paddle",
        "package_version": _package_version("paddleocr"),
        "transformers_version": _package_version("transformers"),
        "torch_version": _package_version("torch"),
        "model_name": args.model_name,
        "engine": "transformers",
        "input_filter": args.id_regex,
        "load_seconds": load_seconds,
        "inference_seconds": total_seconds,
        "mean_seconds_per_line": total_seconds / len(observations) if observations else 0.0,
        "peak_rss_mb": _peak_rss_mb(),
        "downloaded_files": _downloaded_model_files(),
        "observations": observations,
    }
    (output / "paddle-corpus.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
