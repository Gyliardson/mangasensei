"""Summarize 48px-vs-PP-OCRv6 disagreements across the licensed corpus."""

from __future__ import annotations

import argparse
import json
import statistics
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

REVIEWED_ANCHORS = {
    "black_jack_v01_pdf009": ("国家試験に合格しなければいけない",),
    "black_jack_v01_pdf073": ("見事な切り口です教授", "うむ"),
    "black_jack_v01_pdf090": ("はい",),
    "black_jack_v01_pdf145": ("春日部一郎博士",),
    "black_jack_v01_pdf171": ("※ステント＝心臓の血管",),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--paddle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _normalize(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _semantic_core(text: str) -> str:
    normalized = _normalize(text)
    return "".join(
        char
        for char in normalized
        if not unicodedata.category(char).startswith(("P", "Z"))
        and char not in {"=", "＝", "!", "！", "?", "？", "…", "‼"}
    )


def _edit_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _classify(text_48px: str, text_paddle: str) -> tuple[str, int, float]:
    left = _normalize(text_48px)
    right = _normalize(text_paddle)
    if not left and not right:
        return "both-empty", 0, 0.0
    if not left:
        return "48px-missing", len(right), 1.0
    if not right:
        return "paddle-empty", len(left), 1.0
    if left == right:
        return "exact", 0, 0.0
    if _semantic_core(left) == _semantic_core(right):
        distance = _edit_distance(left, right)
        return "punctuation-only", distance, distance / max(len(left), len(right))
    distance = _edit_distance(left, right)
    ratio = distance / max(len(left), len(right))
    if distance == 1:
        return "one-char", distance, ratio
    if ratio <= 0.2:
        return "small", distance, ratio
    return "large", distance, ratio


def _contains_anchor(text: str, anchor: str) -> bool:
    return _normalize(anchor) in _normalize(text)


def _observations(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = payload.get("observations")
    if not isinstance(raw, list):
        raise TypeError("Paddle corpus observations must be a list")
    return {str(item["id"]): item for item in raw}


def main() -> None:
    args = _parse_args()
    prepared_root = args.prepared.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    corpus = json.loads((prepared_root / "corpus.json").read_text(encoding="utf-8"))
    paddle_payload = json.loads(args.paddle.read_text(encoding="utf-8"))
    paddle = _observations(paddle_payload)

    counts: Counter[str] = Counter()
    pages: list[dict[str, Any]] = []
    disagreement_ratios: list[float] = []
    total_lines = 0
    for page in corpus["pages"]:
        label = str(page["label"])
        page_lines: list[dict[str, Any]] = []
        page_counts: Counter[str] = Counter()
        for line in page["lines"]:
            line_id = f"{label}--line-{int(line['index']):03d}"
            paddle_line = paddle.get(line_id)
            if paddle_line is None:
                raise RuntimeError(f"missing Paddle observation for {line_id}")
            result_48px = line.get("ocr_48px")
            text_48px = str(result_48px.get("text", "")) if isinstance(result_48px, dict) else ""
            confidence_48px = (
                float(result_48px.get("confidence", 0.0))
                if isinstance(result_48px, dict)
                else None
            )
            text_paddle = str(paddle_line.get("text", ""))
            confidence_paddle = float(paddle_line.get("confidence", 0.0))
            classification, distance, ratio = _classify(text_48px, text_paddle)
            counts[classification] += 1
            page_counts[classification] += 1
            total_lines += 1
            if classification not in {"exact", "both-empty"}:
                disagreement_ratios.append(ratio)
            page_lines.append(
                {
                    "index": line["index"],
                    "xyxy": line["xyxy"],
                    "geometry": line["geometry"],
                    "direction": line["direction"],
                    "font_size": line["font_size"],
                    "detector_probability": line["detector_probability"],
                    "ocr_48px": {
                        "text": text_48px,
                        "confidence": confidence_48px,
                    },
                    "paddle_v6": {
                        "text": text_paddle,
                        "confidence": confidence_paddle,
                        "seconds": paddle_line["seconds"],
                    },
                    "classification": classification,
                    "edit_distance": distance,
                    "normalized_edit_ratio": ratio,
                    "crop": line["crop"],
                }
            )

        anchor_results = []
        for anchor in REVIEWED_ANCHORS.get(label, ()):
            anchor_results.append(
                {
                    "anchor": anchor,
                    "48px_found": any(
                        _contains_anchor(entry["ocr_48px"]["text"], anchor)
                        for entry in page_lines
                    ),
                    "paddle_found": any(
                        _contains_anchor(entry["paddle_v6"]["text"], anchor)
                        for entry in page_lines
                    ),
                }
            )
        pages.append(
            {
                "label": label,
                "relative_path": page["relative_path"],
                "annotation": page["annotation"],
                "counts": dict(page_counts),
                "reviewed_anchors": anchor_results,
                "lines": page_lines,
            }
        )

    report = {
        "schema_version": 1,
        "source_head": corpus.get("source_head"),
        "totals": {
            "pages": len(pages),
            "detector_lines": total_lines,
            "classifications": dict(counts),
            "exact_or_punctuation_only": counts["exact"] + counts["punctuation-only"],
            "semantic_disagreements": (
                counts["one-char"]
                + counts["small"]
                + counts["large"]
                + counts["48px-missing"]
                + counts["paddle-empty"]
            ),
            "mean_disagreement_ratio": (
                statistics.mean(disagreement_ratios) if disagreement_ratios else 0.0
            ),
        },
        "runtime": {
            key: paddle_payload.get(key)
            for key in (
                "package_version",
                "transformers_version",
                "torch_version",
                "model_name",
                "engine",
                "load_seconds",
                "inference_seconds",
                "mean_seconds_per_line",
                "peak_rss_mb",
                "downloaded_files",
            )
        },
        "pages": pages,
    }
    (output / "paddle-corpus-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [
        "# OCR #54 — 48px vs PP-OCRv6 licensed-corpus audit",
        "",
        f"Source head: `{corpus.get('source_head')}`",
        "",
        "## Aggregate",
        "",
        f"- Detector lines: **{total_lines}**",
        f"- Exact agreement: **{counts['exact']}**",
        f"- Punctuation-only disagreement: **{counts['punctuation-only']}**",
        f"- One-character disagreement: **{counts['one-char']}**",
        f"- Small semantic disagreement: **{counts['small']}**",
        f"- Large semantic disagreement: **{counts['large']}**",
        f"- 48px missing while Paddle returned text: **{counts['48px-missing']}**",
        f"- Paddle empty while 48px returned text: **{counts['paddle-empty']}**",
        "",
        "## Disagreements",
        "",
        "Only non-exact rows are listed below. Compare the line index against the numbered source "
        "image in the prepared artifact before selecting a preferred recognizer.",
        "",
    ]
    for page in pages:
        disagreements = [
            entry
            for entry in page["lines"]
            if entry["classification"] not in {"exact", "both-empty"}
        ]
        if not disagreements:
            continue
        lines.extend([f"### {page['label']}", ""])
        lines.append("| # | Class | 48px | Paddle | 48 conf | Paddle conf | Edit ratio |")
        lines.append("|---:|---|---|---|---:|---:|---:|")
        for entry in disagreements:
            left = entry["ocr_48px"]["text"].replace("|", "\\|")
            right = entry["paddle_v6"]["text"].replace("|", "\\|")
            left_conf = entry["ocr_48px"]["confidence"]
            left_conf_text = "-" if left_conf is None else f"{left_conf:.4f}"
            lines.append(
                f"| {entry['index']} | {entry['classification']} | `{left}` | `{right}` | "
                f"{left_conf_text} | {entry['paddle_v6']['confidence']:.4f} | "
                f"{entry['normalized_edit_ratio']:.3f} |"
            )
        lines.append("")
    (output / "paddle-corpus-report.md").write_text("\n".join(lines), encoding="utf-8")
    print(
        "OCR_PADDLE_CORPUS_SUMMARY "
        f"pages={len(pages)} lines={total_lines} semantic_disagreements="
        f"{report['totals']['semantic_disagreements']}"
    )


if __name__ == "__main__":
    main()
