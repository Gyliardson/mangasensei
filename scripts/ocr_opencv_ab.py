"""Capture and compare controlled OpenCV OCR migration diagnostics."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from PIL import Image

from mangasensei.domain.models import PageDimensions
from mangasensei.ocr.adapter.manga_image_translator import (
    _DETECTOR_FLAGS,
    _RECOGNIZER_FLAG,
    MangaImageTranslatorEngine,
    _decode_rgb,
    _manga_reading_order,
    region_from_upstream,
)
from mangasensei.ocr.diagnostics.opencv_ab import (
    array_descriptor,
    array_fingerprint,
    compare_probe_roots,
    safe_comparison_summary,
    source_zone_statistics,
    validate_artifact_root,
    write_fixture_artifact,
    write_fixture_notice,
    write_probe_manifest,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "ocr" / "real_manga" / "black_jack"
FIXTURE_MANIFEST_PATH = FIXTURE_ROOT / "manifest.json"
MODEL_MANIFEST_PATH = (
    REPOSITORY_ROOT
    / "backend"
    / "src"
    / "mangasensei"
    / "ocr"
    / "models"
    / "manifest.json"
)
_PAGE9_FILE = "v01/black_jack_v01_pdf009.jpg"
_REVIEWED_MAP_ZONES = {
    _PAGE9_FILE: {
        "page9_boten_edge_band": (258, 930, 272, 1220),
        "page9_boten_context": (250, 930, 285, 1220),
    }
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser("probe", help="capture all 12 licensed OCR fixtures")
    probe.add_argument("--output", type=Path, required=True)
    probe.add_argument("--model-cache", type=Path, default=Path("var/models"))

    compare = subparsers.add_parser("compare", help="compare two same-code probe roots")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser


@dataclass(slots=True)
class _RuntimeCapture:
    recognizer: Any
    arrays: dict[str, np.ndarray] = field(default_factory=dict)
    array_stages: dict[str, str] = field(default_factory=dict)
    preprocessing: list[dict[str, Any]] = field(default_factory=list)
    detector_forwards: list[dict[str, Any]] = field(default_factory=list)
    crops: list[dict[str, Any]] = field(default_factory=list)
    recognizer_inputs: list[dict[str, Any]] = field(default_factory=list)
    recognizer_batches: list[dict[str, Any]] = field(default_factory=list)
    _pending_crop_indices: list[int] = field(default_factory=list)

    @contextmanager
    def installed(self) -> Iterator[None]:
        from mangasensei.ocr.adapter import recognizer_48px as recognizer_boundary
        from mangasensei.ocr.vendor.manga_image_translator.manga_translator.detection import (
            default as detector_module,
        )

        imgproc = detector_module.imgproc
        original_resize = imgproc.resize_aspect_ratio
        original_forward = detector_module.det_batch_forward_default
        original_crop = recognizer_boundary._RecognitionQuadrilateral.get_transformed_region
        model = self.recognizer.model
        model_had_override = "infer_beam_batch_tensor" in vars(model)
        model_override = vars(model).get("infer_beam_batch_tensor")
        original_tensor_inference = model.infer_beam_batch_tensor

        def capture_resize(
            image: np.ndarray,
            square_size: int,
            interpolation: int,
            mag_ratio: float = 1,
        ) -> tuple[np.ndarray, float, tuple[int, int], int, int]:
            result = original_resize(image, square_size, interpolation, mag_ratio)
            resized, ratio, heatmap_size, pad_width, pad_height = result
            call_index = len(self.preprocessing)
            filtered_key = self._store_array(
                f"detector_filtered_{call_index:03d}", image
            )
            resized_key = self._store_array(f"detector_resized_{call_index:03d}", resized)
            if call_index == 0:
                self.array_stages["detector_filtered_input"] = filtered_key
                self.array_stages["detector_resized_input"] = resized_key
            self.preprocessing.append(
                {
                    "call_index": call_index,
                    "square_size": int(square_size),
                    "interpolation": int(interpolation),
                    "magnification_ratio": float(mag_ratio),
                    "input": array_descriptor(image),
                    "output": array_descriptor(resized),
                    "resize_ratio": float(ratio),
                    "heatmap_size": [int(value) for value in heatmap_size],
                    "pad_width": int(pad_width),
                    "pad_height": int(pad_height),
                }
            )
            return result

        def capture_forward(batch: Any, device: str) -> tuple[np.ndarray, np.ndarray]:
            batch_array = np.asarray(batch)
            db_map, auxiliary_mask = original_forward(batch, device)
            call_index = len(self.detector_forwards)
            db_key = self._store_array(f"detector_db_{call_index:03d}", db_map)
            auxiliary_key = self._store_array(
                f"detector_auxiliary_mask_{call_index:03d}", auxiliary_mask
            )
            if call_index == 0:
                self.array_stages["detector_db"] = db_key
                self.array_stages["detector_auxiliary_mask"] = auxiliary_key
            self.detector_forwards.append(
                {
                    "call_index": call_index,
                    "device": str(device),
                    "input": array_descriptor(batch_array),
                    "db": array_descriptor(db_map),
                    "auxiliary_mask": array_descriptor(auxiliary_mask),
                }
            )
            return db_map, auxiliary_mask

        def capture_crop(
            quadrilateral: Any,
            image: np.ndarray,
            direction: str,
            text_height: int,
        ) -> np.ndarray:
            crop = original_crop(quadrilateral, image, direction, text_height)
            crop_index = len(self.crops)
            key = self._store_array(f"recognizer_crop_{crop_index:03d}", crop)
            self.crops.append(
                {
                    "crop_index": crop_index,
                    "bbox": _bbox_values(quadrilateral.xyxy),
                    "points": _points(quadrilateral.pts),
                    "direction": str(direction),
                    "text_height": int(text_height),
                    "array_key": key,
                    "array": array_descriptor(crop),
                }
            )
            self._pending_crop_indices.append(crop_index)
            return crop

        def capture_tensor_inference(
            image_tensor: torch.Tensor,
            image_widths: Sequence[int],
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            tensor = image_tensor.detach().cpu().numpy()
            call_index = len(self.recognizer_batches)
            batch_key = self._store_array(f"recognizer_batch_{call_index:03d}", tensor)
            self.array_stages[f"recognizer_batch_{call_index:03d}"] = batch_key
            batch_inputs: list[dict[str, Any]] = []
            for batch_index, width_value in enumerate(image_widths):
                width = int(width_value)
                valid_tensor = np.ascontiguousarray(tensor[batch_index, :, :, :width])
                pixels = np.rint(
                    np.transpose(valid_tensor, (1, 2, 0)) * 127.5 + 127.5
                ).clip(0, 255).astype(np.uint8)
                crop_index = self._claim_crop(pixels)
                input_index = len(self.recognizer_inputs)
                input_key = self._store_array(
                    f"recognizer_input_{input_index:03d}", valid_tensor
                )
                crop_record = self.crops[crop_index]
                input_record = {
                    "input_index": input_index,
                    "inference_call": call_index,
                    "batch_index": batch_index,
                    "crop_index": crop_index,
                    "bbox": crop_record["bbox"],
                    "width": width,
                    "array_key": input_key,
                    "array": array_descriptor(valid_tensor),
                }
                self.recognizer_inputs.append(input_record)
                batch_inputs.append(input_record)
            self.recognizer_batches.append(
                {
                    "call_index": call_index,
                    "widths": [int(value) for value in image_widths],
                    "array_key": batch_key,
                    "array": array_descriptor(tensor),
                    "inputs": batch_inputs,
                }
            )
            return original_tensor_inference(image_tensor, image_widths, *args, **kwargs)

        imgproc.resize_aspect_ratio = capture_resize
        detector_module.det_batch_forward_default = capture_forward
        recognizer_boundary._RecognitionQuadrilateral.get_transformed_region = capture_crop
        model.infer_beam_batch_tensor = capture_tensor_inference
        try:
            yield
        finally:
            imgproc.resize_aspect_ratio = original_resize
            detector_module.det_batch_forward_default = original_forward
            recognizer_boundary._RecognitionQuadrilateral.get_transformed_region = original_crop
            if model_had_override:
                model.infer_beam_batch_tensor = model_override
            else:
                delattr(model, "infer_beam_batch_tensor")

    def store_raw_mask(self, raw_mask: np.ndarray) -> dict[str, object]:
        key = self._store_array("detector_raw_mask", raw_mask)
        self.array_stages["detector_raw_mask"] = key
        return array_descriptor(raw_mask)

    def reviewed_map_zones(
        self,
        *,
        fixture_file: str,
        source_width: int,
        source_height: int,
    ) -> dict[str, object]:
        zones = _REVIEWED_MAP_ZONES.get(fixture_file, {})
        if not zones or not self.preprocessing or not self.detector_forwards:
            return {}
        preprocessing = self.preprocessing[0]
        db_key = self.array_stages["detector_db"]
        detector_shape = preprocessing["output"]["shape"]
        return {
            name: source_zone_statistics(
                self.arrays[db_key],
                source_width=source_width,
                source_height=source_height,
                resize_ratio=float(preprocessing["resize_ratio"]),
                detector_input_width=int(detector_shape[1]),
                detector_input_height=int(detector_shape[0]),
                source_zone=zone,
            )
            for name, zone in zones.items()
        }

    def _claim_crop(self, pixels: np.ndarray) -> int:
        fingerprint = array_fingerprint(pixels)
        for pending_index, crop_index in enumerate(self._pending_crop_indices):
            if self.crops[crop_index]["array"]["fingerprint"] == fingerprint:
                self._pending_crop_indices.pop(pending_index)
                return crop_index
        raise RuntimeError("normalized recognizer input no longer matches a transformed crop")

    def _store_array(self, key: str, value: np.ndarray) -> str:
        if key in self.arrays:
            raise RuntimeError(f"duplicate diagnostic array key: {key}")
        self.arrays[key] = np.ascontiguousarray(value).copy()
        return key


async def capture_probe(output: Path, *, model_cache: Path) -> dict[str, object]:
    output_root = validate_artifact_root(output, repository_root=REPOSITORY_ROOT)
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"diagnostic output directory is not empty: {output_root}")

    fixture_manifest = _read_json(FIXTURE_MANIFEST_PATH)
    model_manifest_payload = _read_json(MODEL_MANIFEST_PATH)
    source = _object(fixture_manifest, "source")
    terms = _object(fixture_manifest, "terms")
    write_fixture_notice(
        output_root,
        source_url=str(terms["url"]),
        work=str(source["workTitleEn"]),
        author=str(source["authorEn"]),
    )

    engine = MangaImageTranslatorEngine(model_cache=_resolve_path(model_cache), device="cpu")
    detector, recognizer, merge, model_manifest = await engine._ensure_loaded()
    provenance = engine.provenance_for_manifest(model_manifest)
    fixture_entries: list[dict[str, str]] = []
    total_started = time.perf_counter()
    for fixture in _fixture_list(fixture_manifest):
        fixture_file = str(fixture["file"])
        fixture_entry = await _capture_fixture(
            engine=engine,
            detector=detector,
            recognizer=recognizer,
            merge=merge,
            model_manifest=model_manifest,
            fixture_file=fixture_file,
            expected_sha256=str(fixture["sha256"]),
            output_root=output_root,
        )
        fixture_entries.append(fixture_entry)

    metadata: dict[str, object] = {
        "schema_version": 1,
        "repository_sha": _repository_sha(),
        "fixture_manifest_sha256": _file_sha256(FIXTURE_MANIFEST_PATH),
        "model_manifest_sha256": _file_sha256(MODEL_MANIFEST_PATH),
        "model_manifest_version": model_manifest.version,
        "model_upstream_commit": model_manifest.upstream_commit,
        "model_artifacts": model_manifest_payload["artifacts"],
        "ocr_config_digest": provenance.config_digest.hex(),
        "opencv": _opencv_metadata(),
        "runtime": _runtime_metadata(),
        "fixture_count": len(fixture_entries),
        "elapsed_seconds": time.perf_counter() - total_started,
        "peak_rss_bytes": _peak_rss_bytes(),
    }
    write_probe_manifest(output_root, metadata=metadata, fixtures=fixture_entries)
    return metadata


async def _capture_fixture(
    *,
    engine: MangaImageTranslatorEngine,
    detector: Any,
    recognizer: Any,
    merge: Any,
    model_manifest: Any,
    fixture_file: str,
    expected_sha256: str,
    output_root: Path,
) -> dict[str, str]:
    image_path = FIXTURE_ROOT / fixture_file
    content = image_path.read_bytes()
    image_sha256 = hashlib.sha256(content).hexdigest()
    if image_sha256 != expected_sha256:
        raise ValueError(f"fixture checksum mismatch: {fixture_file}")
    pixels = _decode_rgb(content)
    with Image.open(image_path) as source:
        dimensions = PageDimensions(width=source.width, height=source.height)

    capture = _RuntimeCapture(recognizer)
    started = time.perf_counter()
    with capture.installed():
        textlines, raw_mask, _ = await detector.detect(
            pixels,
            engine._detection_size,
            engine._text_threshold,
            engine._box_threshold,
            engine._unclip_ratio,
            *_DETECTOR_FLAGS,
        )
        detector_candidates = [
            _quadrilateral_record(line, include_text=False) for line in textlines
        ]
        raw_mask_record = capture.store_raw_mask(raw_mask)
        recognized = await recognizer.recognize(
            pixels,
            textlines,
            engine._ocr_config,
            _RECOGNIZER_FLAG,
        )
        recognized = [line for line in recognized if str(line.text).strip()]
        recognized_records = [
            _quadrilateral_record(line, include_text=True) for line in recognized
        ]
        merged = await merge(recognized, dimensions.width, dimensions.height)
        merged_records = [_merged_record(block) for block in merged]
        ordered = _manga_reading_order(merged, page_height=dimensions.height)
        final_regions = tuple(
            region_from_upstream(
                region,
                image_sha256=image_sha256,
                dimensions=dimensions,
                reading_order=index,
                upstream_commit=model_manifest.upstream_commit,
            )
            for index, region in enumerate(ordered[:128])
            if str(region.text).strip()
        )

    record: dict[str, object] = {
        "image_sha256": image_sha256,
        "dimensions": dimensions.model_dump(),
        "elapsed_seconds": time.perf_counter() - started,
        "peak_rss_bytes": _peak_rss_bytes(),
        "detector": {
            "preprocessing": capture.preprocessing,
            "forwards": capture.detector_forwards,
            "candidate_count": len(detector_candidates),
            "candidates": detector_candidates,
            "raw_mask": raw_mask_record,
            "reviewed_map_zones": capture.reviewed_map_zones(
                fixture_file=fixture_file,
                source_width=dimensions.width,
                source_height=dimensions.height,
            ),
        },
        "recognizer": {
            "crop_count": len(capture.crops),
            "crops": capture.crops,
            "inputs": capture.recognizer_inputs,
            "batches": capture.recognizer_batches,
            "accepted_count": len(recognized_records),
            "accepted": recognized_records,
        },
        "merge": {
            "region_count": len(merged_records),
            "regions": merged_records,
        },
        "final_regions": [_final_record(region) for region in final_regions],
        "array_stages": capture.array_stages,
    }
    return write_fixture_artifact(
        output_root,
        fixture_file=fixture_file,
        record=record,
        arrays=capture.arrays,
    )


def compare_and_write(baseline: Path, candidate: Path, output: Path) -> dict[str, Any]:
    baseline_root = validate_artifact_root(baseline, repository_root=REPOSITORY_ROOT)
    candidate_root = validate_artifact_root(candidate, repository_root=REPOSITORY_ROOT)
    output_path = validate_artifact_root(output, repository_root=REPOSITORY_ROOT)
    comparison = compare_probe_roots(baseline_root, candidate_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return comparison


def _quadrilateral_record(line: Any, *, include_text: bool) -> dict[str, object]:
    record: dict[str, object] = {
        "bbox": _bbox_values(line.xyxy),
        "points": _points(line.pts),
        "score": float(line.prob),
        "area": float(line.area),
        "direction": str(line.direction),
    }
    if include_text:
        record["text"] = str(line.text)
        record["confidence"] = float(line.prob)
    return record


def _merged_record(block: Any) -> dict[str, object]:
    return {
        "bbox": _bbox_values(block.xyxy),
        "lines": np.asarray(block.lines).astype(int).tolist(),
        "text": str(block.text),
        "confidence": float(block.prob),
        "angle": float(block.angle),
    }


def _final_record(region: Any) -> dict[str, object]:
    bbox = region.bbox
    return {
        "id": str(region.id),
        "bbox": [bbox.x, bbox.y, bbox.x + bbox.width, bbox.y + bbox.height],
        "polygon": region.polygon,
        "text": region.japanese_text,
        "confidence": region.confidence,
        "reading_order": region.reading_order,
    }


def _bbox_values(values: Any) -> list[float]:
    return [float(value) for value in values]


def _points(values: Any) -> list[list[int]]:
    return np.asarray(values).astype(int).tolist()


def _opencv_metadata() -> dict[str, object]:
    build_information = cv2.getBuildInformation().encode("utf-8")
    return {
        "distribution_version": importlib.metadata.version("opencv-python-headless"),
        "runtime_version": cv2.__version__,
        "build_information_sha256": hashlib.sha256(build_information).hexdigest(),
        "thread_count": int(cv2.getNumThreads()),
        "optimized": bool(cv2.useOptimized()),
    }


def _runtime_metadata() -> dict[str, object]:
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "numpy": np.__version__,
        "torch": torch.__version__,
        "torch_threads": torch.get_num_threads(),
        "torch_interop_threads": torch.get_num_interop_threads(),
    }


def _peak_rss_bytes() -> int | None:
    if os.name == "nt":
        class _ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(_ProcessMemoryCounters),
            ctypes.c_ulong,
        )
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        process = kernel32.GetCurrentProcess()
        succeeded = psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        return int(counters.PeakWorkingSetSize) if succeeded else None
    try:
        import resource
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(usage if sys.platform == "darwin" else usage * 1024)


def _repository_sha() -> str:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to bind the probe to exact source code")
    completed = subprocess.run(  # noqa: S603
        (git, "rev-parse", "HEAD"),
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_path(path: Path) -> Path:
    return path.resolve()


def _read_json(path: Path) -> dict[str, Any]:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"expected JSON object: {path}")
    return raw


def _object(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"expected object at {key}")
    return value


def _fixture_list(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = payload.get("fixtures")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("fixture manifest fixtures must be a list of objects")
    return value


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "probe":
        metadata = asyncio.run(capture_probe(args.output, model_cache=args.model_cache))
        opencv = _object(metadata, "opencv")
        print(
            "OCR_OPENCV_PROBE "
            f"opencv={opencv['distribution_version']} "
            f"fixture_count={metadata['fixture_count']} "
            f"elapsed_seconds={float(metadata['elapsed_seconds']):.3f}"
        )
        return
    comparison = compare_and_write(args.baseline, args.candidate, args.output)
    print(f"OCR_OPENCV_COMPARE {safe_comparison_summary(comparison)}")


if __name__ == "__main__":
    main()
