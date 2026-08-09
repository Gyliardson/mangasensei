"""Summarize PP-OCRv6 crop-height/context sweep over reviewed line cases."""

from __future__ import annotations

import argparse
import json
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--paddle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _semantic_core(text: str) -> str:
    return "".join(
        char
        for char in _normalize(text)
        if not unicodedata.category(char).startswith(("P", "Z"))
        and char not in {"=", "!", "?", "…", "‼"}
    )


def _observations(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = payload.get("observations")
    if not isinstance(raw, list):
        raise TypeError("Paddle observations must be a list")
    return {str(item["id"]): item for item in raw}


def main() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prepared = json.loads((args.prepared / "corpus.json").read_text(encoding="utf-8"))
    paddle_payload = json.loads(args.paddle.read_text(encoding="utf-8"))
    paddle = _observations(paddle_payload)

    variant_scores: dict[str, dict[str, int]] = defaultdict(
        lambda: {"exact": 0, "semantic": 0, "negative_pass": 0, "cases": 0}
    )
    case_reports: list[dict[str, Any]] = []
    for case in prepared["cases"]:
        expected = str(case["expected"])
        variants: list[dict[str, Any]] = []
        for variant in case["variants"]:
            observation = paddle.get(str(variant["id"]))
            if observation is None:
                raise RuntimeError(f"missing Paddle observation for {variant['id']}")
            text = str(observation.get("text", ""))
            negative = not expected
            exact = _normalize(text) == _normalize(expected)
            semantic = (
                not text.strip()
                if negative
                else _semantic_core(text) == _semantic_core(expected)
            )
            variant_key = f"{variant['context']}-h{variant['textheight']}"
            score = variant_scores[variant_key]
            score["cases"] += 1
            score["exact"] += int(exact)
            score["semantic"] += int(semantic)
            score["negative_pass"] += int(negative and not text.strip())
            variants.append(
                {
                    **variant,
                    "text": text,
                    "confidence": float(observation.get("confidence", 0.0)),
                    "seconds": float(observation.get("seconds", 0.0)),
                    "exact": exact,
                    "semantic": semantic,
                }
            )
        case_reports.append(
            {
                "page": case["page"],
                "line_index": case["line_index"],
                "role": case["role"],
                "expected": expected,
                "variants": variants,
            }
        )

    report = {
        "schema_version": 1,
        "source_head": prepared.get("source_head"),
        "variant_scores": dict(variant_scores),
        "runtime": {
            key: paddle_payload.get(key)
            for key in (
                "package_version",
                "transformers_version",
                "torch_version",
                "model_name",
                "load_seconds",
                "inference_seconds",
                "mean_seconds_per_line",
                "peak_rss_mb",
            )
        },
        "cases": case_reports,
    }
    (output / "paddle-scale-sweep.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# OCR #54 — PP-OCRv6 crop-scale sweep",
        "",
        f"Source head: `{prepared.get('source_head')}`",
        "",
        "## Variant scores",
        "",
        "| Variant | Exact | Semantic | Negative rejects | Cases |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in sorted(variant_scores):
        score = variant_scores[key]
        lines.append(
            f"| {key} | {score['exact']} | {score['semantic']} | "
            f"{score['negative_pass']} | {score['cases']} |"
        )
    lines.extend(["", "## Per-case outputs", ""])
    for case in case_reports:
        lines.extend(
            [
                f"### {case['page']} #{case['line_index']} — {case['role']}",
                "",
                f"Expected: `{case['expected']}`",
                "",
                "| Variant | Output | Exact | Semantic | Confidence | Seconds |",
                "|---|---|---:|---:|---:|---:|",
            ]
        )
        for variant in case["variants"]:
            text = variant["text"].replace("|", "\\|")
            key = f"{variant['context']}-h{variant['textheight']}"
            lines.append(
                f"| {key} | `{text}` | {'yes' if variant['exact'] else 'no'} | "
                f"{'yes' if variant['semantic'] else 'no'} | {variant['confidence']:.4f} | "
                f"{variant['seconds']:.3f} |"
            )
        lines.append("")
    (output / "paddle-scale-sweep.md").write_text("\n".join(lines), encoding="utf-8")
    print(
        "OCR_PADDLE_SCALE_SUMMARY "
        f"cases={len(case_reports)} variants={len(variant_scores)}"
    )


if __name__ == "__main__":
    main()
