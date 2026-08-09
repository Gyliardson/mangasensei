"""Summarize recognizer benchmark outputs without emitting fixture transcripts to logs."""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--manga-ocr", type=Path, required=True)
    parser.add_argument("--paddle-v6", type=Path, required=True)
    parser.add_argument("--baberu", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _normalize(text: str) -> str:
    text = re.sub(r"\s+", "", text)
    return text.replace("…", "...").replace("‼", "!!")


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


def _anchor_cer(expected: str, actual: str) -> float:
    expected_norm = _normalize(expected)
    actual_norm = _normalize(actual)
    if not expected_norm:
        return 0.0
    if expected_norm in actual_norm:
        return 0.0
    if len(actual_norm) >= len(expected_norm):
        window_size = len(expected_norm)
        best = min(
            _edit_distance(expected_norm, actual_norm[index : index + window_size])
            for index in range(len(actual_norm) - window_size + 1)
        )
    else:
        best = _edit_distance(expected_norm, actual_norm)
    return best / len(expected_norm)


def _passes(expected: str, actual: str) -> bool:
    return _normalize(expected) in _normalize(actual)


def _observation_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise TypeError("recognizer observations must be a list")
    return {str(item["id"]): item for item in observations}


def _ordered_candidate_indices(case: dict[str, Any]) -> list[int]:
    candidates = case["candidates"]
    if not isinstance(candidates, list):
        raise TypeError("case candidates must be a list")
    if not candidates:
        return []
    vertical_count = sum(candidate["direction"] == "v" for candidate in candidates)
    if vertical_count >= len(candidates) / 2:
        return sorted(
            range(len(candidates)),
            key=lambda index: (
                -((candidates[index]["xyxy"][0] + candidates[index]["xyxy"][2]) / 2),
                candidates[index]["xyxy"][1],
            ),
        )
    return sorted(
        range(len(candidates)),
        key=lambda index: (
            candidates[index]["xyxy"][1],
            candidates[index]["xyxy"][0],
        ),
    )


def _combined_48px(case: dict[str, Any], key: str) -> str:
    candidates = case["candidates"]
    parts: list[str] = []
    for index in _ordered_candidate_indices(case):
        result = candidates[index].get(key)
        if isinstance(result, dict):
            text = str(result.get("text", ""))
            if text:
                parts.append(text)
    return "".join(parts)


def _combined_external(
    case: dict[str, Any],
    observations: dict[str, dict[str, Any]],
    suffix: str,
) -> str:
    parts: list[str] = []
    for index in _ordered_candidate_indices(case):
        observation = observations.get(f"{case['name']}--line-{index:02d}--{suffix}")
        if observation:
            text = str(observation.get("text", ""))
            if text:
                parts.append(text)
    return "".join(parts)


def _block_external(
    case_name: str,
    observations: dict[str, dict[str, Any]],
) -> str:
    return str(observations.get(f"{case_name}--block", {}).get("text", ""))


def _median_seconds(observations: dict[str, dict[str, Any]], case_name: str) -> float | None:
    values = [
        float(item["seconds"])
        for item in observations.values()
        if item.get("case") == case_name
    ]
    return statistics.median(values) if values else None


def _consensus(predictions: dict[str, str]) -> dict[str, Any]:
    normalized = {name: _normalize(text) for name, text in predictions.items() if text}
    counts = Counter(normalized.values())
    if not counts:
        return {"winner": "", "votes": 0, "voters": [], "unanimous": False}
    winner, votes = counts.most_common(1)[0]
    voters = [name for name, text in normalized.items() if text == winner]
    return {
        "winner": winner,
        "votes": votes,
        "voters": voters,
        "unanimous": votes == len(normalized),
    }


def main() -> None:
    args = _parse_args()
    prepared_root = args.prepared.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    benchmark = json.loads((prepared_root / "benchmark.json").read_text(encoding="utf-8"))
    manga_payload = json.loads(args.manga_ocr.read_text(encoding="utf-8"))
    paddle_payload = json.loads(args.paddle_v6.read_text(encoding="utf-8"))
    baberu_payload = json.loads(args.baberu.read_text(encoding="utf-8"))
    manga = _observation_map(manga_payload)
    paddle = _observation_map(paddle_payload)
    baberu = _observation_map(baberu_payload)

    case_reports: list[dict[str, Any]] = []
    for case in benchmark["cases"]:
        name = str(case["name"])
        expected = str(case["expected"])
        predictions = {
            "48px-tight": _combined_48px(case, "tight_48px"),
            "48px-context": _combined_48px(case, "context_48px"),
            "manga-ocr-tight-lines": _combined_external(case, manga, "tight"),
            "manga-ocr-context-lines": _combined_external(case, manga, "context"),
            "manga-ocr-block": _block_external(name, manga),
            "paddle-v6-tight": _combined_external(case, paddle, "tight"),
            "paddle-v6-context": _combined_external(case, paddle, "context"),
            "baberu-block": _block_external(name, baberu),
        }
        evaluations = {
            recognizer: {
                "passes_anchor": _passes(expected, text),
                "anchor_cer": _anchor_cer(expected, text),
                "normalized_length": len(_normalize(text)),
            }
            for recognizer, text in predictions.items()
        }
        context_experts = {
            "48px-context": predictions["48px-context"],
            "manga-ocr-block": predictions["manga-ocr-block"],
            "paddle-v6-context": predictions["paddle-v6-context"],
            "baberu-block": predictions["baberu-block"],
        }
        case_reports.append(
            {
                "name": name,
                "expected": expected,
                "detector_candidate_count": case["detector_candidate_count"],
                "predictions": predictions,
                "evaluations": evaluations,
                "tight_context_stability": {
                    "48px": _normalize(predictions["48px-tight"])
                    == _normalize(predictions["48px-context"]),
                    "manga-ocr-lines": _normalize(predictions["manga-ocr-tight-lines"])
                    == _normalize(predictions["manga-ocr-context-lines"]),
                    "paddle-v6": _normalize(predictions["paddle-v6-tight"])
                    == _normalize(predictions["paddle-v6-context"]),
                },
                "context_consensus": _consensus(context_experts),
                "median_seconds": {
                    "manga-ocr": _median_seconds(manga, name),
                    "paddle-v6": _median_seconds(paddle, name),
                    "baberu": _median_seconds(baberu, name),
                },
            }
        )

    recognizer_names = list(case_reports[0]["predictions"]) if case_reports else []
    aggregate = {}
    for recognizer in recognizer_names:
        successes = sum(
            bool(case["evaluations"][recognizer]["passes_anchor"])
            for case in case_reports
        )
        aggregate[recognizer] = {
            "successes": successes,
            "cases": len(case_reports),
            "success_rate": successes / len(case_reports) if case_reports else 0.0,
            "mean_anchor_cer": statistics.mean(
                case["evaluations"][recognizer]["anchor_cer"] for case in case_reports
            )
            if case_reports
            else 0.0,
        }

    report = {
        "schema_version": 2,
        "source_head": benchmark.get("source_head"),
        "aggregate": aggregate,
        "manga_ocr_runtime": {
            key: manga_payload.get(key)
            for key in (
                "package_version",
                "transformers_version",
                "model_revision",
                "model_size",
                "model_sha256",
                "load_seconds",
                "peak_rss_mb",
            )
        },
        "paddle_v6_runtime": {
            key: paddle_payload.get(key)
            for key in (
                "package_version",
                "transformers_version",
                "model_name",
                "engine",
                "load_seconds",
                "peak_rss_mb",
            )
        },
        "baberu_runtime": {
            key: baberu_payload.get(key)
            for key in (
                "model_revision",
                "tier",
                "verified_files",
                "load_seconds",
                "peak_rss_mb",
            )
        },
        "cases": case_reports,
    }
    (output / "ensemble-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# OCR #54 recognizer benchmark",
        "",
        f"Source head: `{benchmark.get('source_head')}`",
        "",
        "## Aggregate",
        "",
        "| Recognizer/view | Passed challenge anchors | Mean anchor CER |",
        "|---|---:|---:|",
    ]
    for recognizer, stats in aggregate.items():
        lines.append(
            f"| {recognizer} | {stats['successes']}/{stats['cases']} | "
            f"{stats['mean_anchor_cer']:.3f} |"
        )
    lines.extend(["", "## Per-case transcript audit", ""])
    for case in case_reports:
        lines.extend(
            [
                f"### {case['name']}",
                "",
                f"Expected reviewed anchor: `{case['expected']}`",
                "",
                "| Recognizer/view | Output | Anchor | CER |",
                "|---|---|---:|---:|",
            ]
        )
        for recognizer, text in case["predictions"].items():
            evaluation = case["evaluations"][recognizer]
            escaped = text.replace("|", "\\|")
            lines.append(
                f"| {recognizer} | `{escaped}` | "
                f"{'PASS' if evaluation['passes_anchor'] else 'FAIL'} | "
                f"{evaluation['anchor_cer']:.3f} |"
            )
        consensus = case["context_consensus"]
        lines.extend(
            [
                "",
                f"Context-expert consensus votes: **{consensus['votes']}**; "
                f"voters: `{', '.join(consensus['voters'])}`.",
                "",
            ]
        )
    (output / "ensemble-report.md").write_text("\n".join(lines), encoding="utf-8")

    summary = {
        "case_count": len(case_reports),
        "aggregate": aggregate,
        "all_context_48px_pass": all(
            case["evaluations"]["48px-context"]["passes_anchor"] for case in case_reports
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_ENSEMBLE_SUMMARY "
        f"cases={len(case_reports)} recognizers={len(recognizer_names)}"
    )


if __name__ == "__main__":
    main()
