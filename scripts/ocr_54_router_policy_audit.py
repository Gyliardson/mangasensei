"""Evaluate a conservative 48px + PP-OCRv6 routing policy on reviewed licensed lines."""

from __future__ import annotations

import argparse
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sudachipy import dictionary, tokenizer

SMALL_KANA = frozenset("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ")


@dataclass(frozen=True, slots=True)
class ReviewedCase:
    page: str
    line_index: int
    expected: str
    role: str


REVIEWED_CASES = (
    ReviewedCase("black_jack_v01_pdf007", 3, "じゃあ僕の", "primary-small-kana"),
    ReviewedCase("black_jack_v01_pdf009", 3, "術野を広げろ", "secondary-extension"),
    ReviewedCase("black_jack_v01_pdf009", 11, "含まれていない‼", "punctuation"),
    ReviewedCase("black_jack_v01_pdf021", 7, "", "reject-keypad"),
    ReviewedCase("black_jack_v01_pdf066", 6, "だと思って", "primary-small-kana"),
    ReviewedCase("black_jack_v01_pdf066", 13, "20整形", "secondary-low-primary"),
    ReviewedCase("black_jack_v01_pdf066", 14, "1内科/麻酔科", "secondary-only-signage"),
    ReviewedCase("black_jack_v01_pdf066", 16, "", "ignore-environmental-english"),
    ReviewedCase("black_jack_v01_pdf066", 25, "医者ってやつは", "primary-small-kana"),
    ReviewedCase("black_jack_v01_pdf073", 11, "切り口です", "single-char-confusable"),
    ReviewedCase("black_jack_v01_pdf090", 7, "じいちゃんもきっと", "primary-small-kana"),
    ReviewedCase("black_jack_v01_pdf123", 14, "なかったとしても", "primary-small-kana"),
    ReviewedCase("black_jack_v01_pdf145", 0, "春日部一郎博士", "single-char-confusable"),
    ReviewedCase("black_jack_v01_pdf145", 5, "第十一代主任教授", "secondary-only-recall"),
    ReviewedCase(
        "black_jack_v01_pdf171",
        5,
        "※ステント＝心臓の血管の狭くなった部分をふくらませて血流をよくしたあと、再びその部分が",
        "primary-long-line",
    ),
    ReviewedCase("black_jack_v01_pdf194", 2, "きったはったの", "primary-small-kana"),
    ReviewedCase("black_jack_v01_pdf201", 0, "", "reject-pattern"),
)


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


def _japanese_char_count(text: str) -> int:
    count = 0
    for char in _normalize(text):
        codepoint = ord(char)
        if (
            0x3040 <= codepoint <= 0x30FF
            or 0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
        ):
            count += 1
    return count


def _delete_small_kana(text: str) -> str:
    return "".join(char for char in _semantic_core(text) if char not in SMALL_KANA)


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


def _semantic_distance(left: str, right: str) -> int:
    return _edit_distance(_semantic_core(left), _semantic_core(right))


def _lexical_score(text: str, sudachi: Any) -> tuple[float, int, int]:
    morphemes = list(sudachi.tokenize(text, tokenizer.Tokenizer.SplitMode.A))
    known_chars = 0
    oov_chars = 0
    for morpheme in morphemes:
        surface = morpheme.surface()
        if morpheme.dictionary_id() < 0:
            oov_chars += len(surface)
        else:
            known_chars += len(surface)
    total = known_chars + oov_chars
    ratio = known_chars / total if total else 0.0
    return ratio, -oov_chars, -len(morphemes)


def _secondary_view(
    line: dict[str, Any],
    observations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    page = str(line["page"])
    index = int(line["index"])
    view = "context" if line["direction"] == "v" else "tight"
    item = observations.get(f"{page}--line-{index:03d}--{view}")
    if item is None:
        raise RuntimeError(f"missing PP-OCRv6 {view} observation for {page}#{index}")
    return item


def _route(
    primary: dict[str, Any] | None,
    secondary: dict[str, Any],
    *,
    sudachi: Any,
) -> tuple[str, str]:
    primary_text = str(primary.get("text", "")) if primary else ""
    primary_conf = float(primary.get("confidence", 0.0)) if primary else 0.0
    secondary_text = str(secondary.get("text", ""))
    secondary_conf = float(secondary.get("confidence", 0.0))
    left = _normalize(primary_text)
    right = _normalize(secondary_text)

    if not left:
        japanese_chars = _japanese_char_count(right)
        if (
            right
            and secondary_conf >= 0.99
            and japanese_chars >= 4
            and japanese_chars / max(len(right), 1) >= 0.7
        ):
            return secondary_text, "secondary-only-high-confidence-japanese"
        return "", "primary-empty-reject-secondary"
    if not right:
        return primary_text, "secondary-empty"
    if left == right:
        return primary_text, "exact-agreement"
    if _semantic_core(left) == _semantic_core(right):
        return primary_text, "punctuation-only-keep-primary"

    left_core = _semantic_core(left)
    right_core = _semantic_core(right)
    if (
        any(char in SMALL_KANA for char in left_core)
        and _delete_small_kana(left_core) == _delete_small_kana(right_core)
        and len(left_core) >= len(right_core)
    ):
        return primary_text, "small-kana-keep-primary"

    if (
        secondary_conf >= 0.95
        and left_core in right_core
        and 1 <= len(right_core) - len(left_core) <= 8
        and _japanese_char_count(right_core) >= 2
    ):
        return secondary_text, "high-confidence-japanese-extension"

    if (
        primary_conf < 0.85
        and secondary_conf >= 0.95
        and len(left_core) == len(right_core)
        and _edit_distance(left_core, right_core) == 1
    ):
        left_score = _lexical_score(left_core, sudachi)
        right_score = _lexical_score(right_core, sudachi)
        if left_score != right_score:
            if right_score > left_score:
                return secondary_text, "single-char-local-lexical-secondary"
            return primary_text, "single-char-local-lexical-primary"

    if (
        len(left_core) <= 10
        and primary_conf < 0.6
        and secondary_conf >= 0.9
        and _japanese_char_count(right_core) >= 2
    ):
        return secondary_text, "short-low-primary-high-secondary-confidence"

    return primary_text, "conservative-primary"


def _paddle_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = payload.get("observations")
    if not isinstance(raw, list):
        raise TypeError("Paddle observations must be a list")
    return {str(item["id"]): item for item in raw}


def main() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    corpus = json.loads((args.prepared / "corpus.json").read_text(encoding="utf-8"))
    paddle_payload = json.loads(args.paddle.read_text(encoding="utf-8"))
    paddle = _paddle_map(paddle_payload)
    sudachi = dictionary.Dictionary(dict="core").create()

    line_map: dict[tuple[str, int], dict[str, Any]] = {}
    for page in corpus["pages"]:
        for line in page["lines"]:
            line_map[(str(page["label"]), int(line["index"]))] = {
                **line,
                "page": str(page["label"]),
            }

    reviewed = []
    baseline_passes = 0
    chosen_passes = 0
    improved = 0
    regressed = 0
    for case in REVIEWED_CASES:
        line = line_map[(case.page, case.line_index)]
        primary = line.get("primary_48px")
        primary_text = str(primary.get("text", "")) if isinstance(primary, dict) else ""
        secondary = _secondary_view(line, paddle)
        chosen, reason = _route(primary, secondary, sudachi=sudachi)
        expected_core = _semantic_core(case.expected)
        primary_core = _semantic_core(primary_text)
        chosen_core = _semantic_core(chosen)
        baseline_passed = primary_core == expected_core
        chosen_passed = chosen_core == expected_core
        baseline_distance = _semantic_distance(primary_text, case.expected)
        chosen_distance = _semantic_distance(chosen, case.expected)
        baseline_passes += int(baseline_passed)
        chosen_passes += int(chosen_passed)
        improved += int(chosen_distance < baseline_distance)
        regressed += int(chosen_distance > baseline_distance)
        reviewed.append(
            {
                "page": case.page,
                "line_index": case.line_index,
                "role": case.role,
                "expected": case.expected,
                "primary": primary,
                "secondary": {
                    "view": "context" if line["direction"] == "v" else "tight",
                    "text": secondary.get("text", ""),
                    "confidence": secondary.get("confidence", 0.0),
                },
                "chosen": chosen,
                "reason": reason,
                "baseline_passed": baseline_passed,
                "chosen_passed": chosen_passed,
                "baseline_distance": baseline_distance,
                "chosen_distance": chosen_distance,
            }
        )

    all_routes: list[dict[str, Any]] = []
    changed = 0
    secondary_only = 0
    for line in line_map.values():
        primary = line.get("primary_48px")
        secondary = _secondary_view(line, paddle)
        chosen, reason = _route(primary, secondary, sudachi=sudachi)
        primary_text = str(primary.get("text", "")) if isinstance(primary, dict) else ""
        if _normalize(chosen) != _normalize(primary_text):
            changed += 1
        if reason == "secondary-only-high-confidence-japanese":
            secondary_only += 1
        all_routes.append(
            {
                "page": line["page"],
                "line_index": line["index"],
                "direction": line["direction"],
                "primary": primary_text,
                "secondary": str(secondary.get("text", "")),
                "chosen": chosen,
                "reason": reason,
            }
        )

    unresolved = len(REVIEWED_CASES) - chosen_passes
    payload = {
        "schema_version": 2,
        "implementation_head": corpus.get("implementation_head"),
        "reviewed": {
            "baseline_passes": baseline_passes,
            "chosen_passes": chosen_passes,
            "cases": len(REVIEWED_CASES),
            "improved": improved,
            "regressed": regressed,
            "unresolved": unresolved,
            "no_regressions": regressed == 0,
            "results": reviewed,
        },
        "corpus_router": {
            "line_count": len(all_routes),
            "changed_from_primary": changed,
            "secondary_only_japanese_rescues": secondary_only,
            "routes": all_routes,
        },
    }
    (output / "router-policy.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_ROUTER_POLICY "
        f"reviewed={chosen_passes}/{len(REVIEWED_CASES)} baseline={baseline_passes} "
        f"improved={improved} regressed={regressed} unresolved={unresolved} "
        f"corpus_lines={len(all_routes)} changed={changed} secondary_only={secondary_only}"
    )
    if regressed:
        raise AssertionError(f"router regressed {regressed} reviewed licensed cases")


if __name__ == "__main__":
    main()
