"""Run the Baberu OCR bubble expert over prepared licensed challenge crops.

Investigation only. Recognized fixture text is written to an Actions artifact, not logs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

BABERU_REPOSITORY = "genshiai-daichi/baberu-ocr"
BABERU_REVISION = "a49f4521d2fcf52a72289e59596213bb67fbb1ae"
_REQUIRED_FILES = (
    "onnx/vision_fp16.onnx",
    "onnx/decoder_prefill_int8.onnx",
    "onnx/decoder_step_int8.onnx",
    "tokenizer/vocab.json",
)
_PAST = [f"past_k{index}" for index in range(6)] + [f"past_v{index}" for index in range(6)]
_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
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
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) / 1024.0


def _metadata_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _download_verified_model(model_root: Path) -> list[dict[str, Any]]:
    from huggingface_hub import HfApi, snapshot_download

    info = HfApi().model_info(
        BABERU_REPOSITORY,
        revision=BABERU_REVISION,
        files_metadata=True,
    )
    metadata = {sibling.rfilename: sibling for sibling in info.siblings}
    snapshot_download(
        repo_id=BABERU_REPOSITORY,
        revision=BABERU_REVISION,
        allow_patterns=list(_REQUIRED_FILES),
        local_dir=model_root,
    )

    verified: list[dict[str, Any]] = []
    for relative_path in _REQUIRED_FILES:
        path = model_root / relative_path
        sibling = metadata.get(relative_path)
        if sibling is None or not path.is_file():
            raise RuntimeError(f"missing pinned Baberu artifact metadata/file: {relative_path}")
        lfs = getattr(sibling, "lfs", None)
        expected_sha = _metadata_value(lfs, "sha256")
        expected_size = _metadata_value(lfs, "size") or getattr(sibling, "size", None)
        if not isinstance(expected_sha, str) or not isinstance(expected_size, int):
            raise RuntimeError(f"missing immutable LFS metadata for Baberu artifact: {relative_path}")
        actual_sha = _sha256(path)
        actual_size = path.stat().st_size
        if actual_sha != expected_sha or actual_size != expected_size:
            raise RuntimeError(
                f"Baberu integrity mismatch for {relative_path}: "
                f"size={actual_size} sha256={actual_sha}"
            )
        verified.append(
            {
                "path": relative_path,
                "size": actual_size,
                "sha256": actual_sha,
            }
        )
    return verified


def _preprocess(image: Image.Image) -> np.ndarray:
    resized = image.convert("RGB").resize((224, 224), Image.Resampling.BICUBIC)
    pixels = np.asarray(resized, dtype=np.float32) / 255.0
    pixels = (pixels - _MEAN) / _STD
    return pixels.transpose(2, 0, 1)[None]


class _Vocab:
    def __init__(self, path: Path) -> None:
        charset = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(charset, list) or not all(isinstance(char, str) for char in charset):
            raise TypeError("Baberu vocabulary must be a JSON string list")
        self._id_to_char = {index + 4: char for index, char in enumerate(charset)}
        self.bos = 1
        self.eos = 2
        self.content_ids = {
            index + 4
            for index, char in enumerate(charset)
            if len(char) == 1
            and char not in "ーｰ〜~"
            and unicodedata.category(char)[0] in "LN"
        }

    def decode(self, token_ids: list[int]) -> str:
        return "".join(self._id_to_char.get(token_id, "") for token_id in token_ids)


class _BaberuOnnx:
    """Minimal local implementation of the model card's published ONNX decode path."""

    def __init__(self, root: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        def session(path: Path) -> Any:
            return ort.InferenceSession(
                str(path),
                options,
                providers=["CPUExecutionProvider"],
            )

        self._vision = session(root / "onnx" / "vision_fp16.onnx")
        self._prefill = session(root / "onnx" / "decoder_prefill_int8.onnx")
        self._step = session(root / "onnx" / "decoder_step_int8.onnx")
        self._vocab = _Vocab(root / "tokenizer" / "vocab.json")

    def __call__(self, image: Image.Image, *, max_new_tokens: int = 128) -> str:
        vision = self._vision.run(
            ["vision_embeds"],
            {"pixel_values": _preprocess(image)},
        )[0]
        output = self._prefill.run(
            None,
            {
                "vision_embeds": vision,
                "input_ids": np.asarray([[self._vocab.bos]], dtype=np.int64),
            },
        )
        logits = output[0][0, -1].astype(np.float64)
        present = output[1:]
        sequence = [self._vocab.bos]
        tokens: list[int] = []
        position = int(vision.shape[1]) + 1

        for _ in range(max_new_tokens):
            for token_id in set(sequence):
                score = logits[token_id]
                logits[token_id] = score * 1.2 if score < 0 else score / 1.2
            if tokens and tokens[-1] in self._vocab.content_ids:
                last = tokens[-1]
                run_length = 0
                for token_id in reversed(tokens):
                    if token_id != last:
                        break
                    run_length += 1
                if run_length >= 12:
                    logits[last] = -np.inf

            next_token = int(np.argmax(logits))
            if next_token == self._vocab.eos:
                break
            tokens.append(next_token)
            sequence.append(next_token)
            feed = {
                "input_ids": np.asarray([[next_token]], dtype=np.int64),
                "position_ids": np.asarray([[position]], dtype=np.int64),
            }
            feed.update({name: value for name, value in zip(_PAST, present, strict=True)})
            output = self._step.run(None, feed)
            logits = output[0][0, -1].astype(np.float64)
            present = output[1:]
            position += 1

        return self._vocab.decode(tokens)


def main() -> None:
    args = _parse_args()
    root = args.input.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    benchmark = json.loads((root / "benchmark.json").read_text(encoding="utf-8"))
    inputs = benchmark.get("inputs")
    if not isinstance(inputs, list):
        raise TypeError("benchmark inputs must be a list")

    model_root = output / "baberu-model"
    model_root.mkdir(parents=True, exist_ok=True)
    verified_files = _download_verified_model(model_root)
    load_started = time.perf_counter()
    recognizer = _BaberuOnnx(model_root)
    load_seconds = time.perf_counter() - load_started

    observations: list[dict[str, Any]] = []
    for item in inputs:
        if item.get("kind") != "block":
            continue
        image_path = root / str(item["path"])
        started = time.perf_counter()
        with Image.open(image_path) as image:
            text = recognizer(image)
        elapsed = time.perf_counter() - started
        observations.append(
            {
                "id": item["id"],
                "case": item["case"],
                "kind": item["kind"],
                "text": text,
                "seconds": elapsed,
                "peak_rss_mb": _peak_rss_mb(),
            }
        )
        print(f"OCR_ENSEMBLE_RUN recognizer=baberu id={item['id']} seconds={elapsed:.3f}")

    payload = {
        "recognizer": "baberu",
        "model_repository": BABERU_REPOSITORY,
        "model_revision": BABERU_REVISION,
        "tier": "vision-fp16-decoder-int8",
        "verified_files": verified_files,
        "load_seconds": load_seconds,
        "peak_rss_mb": _peak_rss_mb(),
        "observations": observations,
    }
    (output / "baberu.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
