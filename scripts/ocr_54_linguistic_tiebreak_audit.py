"""Evaluate conservative local linguistic signals for OCR disagreement routing.

This is investigation-only. It does not autocorrect OCR output and writes reviewed text only
to a short-lived Actions artifact.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sudachipy import dictionary, tokenizer


@dataclass(frozen=True, slots=True)
class Pair:
    name: str
    correct: str
    alternate: str
    class_name: str


PAIRS = (
    Pair("kiriguchi", "切り口です", "切りロです", "kanji-katakana-confusable"),
    Pair("hakase", "春日部一郎博士", "春日部一郎博土", "kanji-confusable"),
    Pair("small-ya", "じゃあ僕の", "じあ僕の", "small-kana-drop"),
    Pair("small-tsu-omotte", "だと思って", "だと思て", "small-kana-drop"),
    Pair("small-tsu-isha", "医者ってやつは", "医者てやつは", "small-kana-drop"),
    Pair("small-kana-jiichan", "じいちゃんもきっと", "じいちんもきと", "small-kana-drop"),
    Pair("small-tsu-nakatta", "なかったとしても", "なかたとしても", "small-kana-drop"),
    Pair("small-tsu-kitta", "きったはったの", "きたはたの", "small-kana-drop"),
    Pair("signage-variant", "1内科/麻酔科", "1内科/麻醉科", "cjk-variant"),
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _analyze(text: str, sudachi: Any) -> dict[str, Any]:
    morphemes = list(sudachi.tokenize(text, tokenizer.Tokenizer.SplitMode.A))
    entries: list[dict[str, Any]] = []
    known_chars = 0
    oov_chars = 0
    content_known_chars = 0
    content_oov_chars = 0
    content_pos = {"名詞", "動詞", "形容詞", "形状詞", "副詞", "連体詞"}
    for morpheme in morphemes:
        surface = morpheme.surface()
        if not surface:
            continue
        dictionary_id = int(morpheme.dictionary_id())
        is_oov = dictionary_id < 0
        pos = tuple(str(item) for item in morpheme.part_of_speech())
        char_count = len(surface)
        if is_oov:
            oov_chars += char_count
        else:
            known_chars += char_count
        if pos and pos[0] in content_pos:
            if is_oov:
                content_oov_chars += char_count
            else:
                content_known_chars += char_count
        entries.append(
            {
                "surface": surface,
                "dictionary_form": morpheme.dictionary_form(),
                "reading": morpheme.reading_form(),
                "dictionary_id": dictionary_id,
                "oov": is_oov,
                "pos": pos,
            }
        )
    total_chars = known_chars + oov_chars
    content_total = content_known_chars + content_oov_chars
    return {
        "morphemes": entries,
        "morpheme_count": len(entries),
        "oov_morpheme_count": sum(bool(entry["oov"]) for entry in entries),
        "known_char_ratio": known_chars / total_chars if total_chars else 0.0,
        "content_known_char_ratio": (
            content_known_chars / content_total if content_total else 0.0
        ),
    }


def _preference(correct: dict[str, Any], alternate: dict[str, Any]) -> str:
    keys = ("content_known_char_ratio", "known_char_ratio")
    for key in keys:
        left = float(correct[key])
        right = float(alternate[key])
        if abs(left - right) >= 0.05:
            return "correct" if left > right else "alternate"
    left_oov = int(correct["oov_morpheme_count"])
    right_oov = int(alternate["oov_morpheme_count"])
    if left_oov != right_oov:
        return "correct" if left_oov < right_oov else "alternate"
    left_count = int(correct["morpheme_count"])
    right_count = int(alternate["morpheme_count"])
    if left_count != right_count:
        return "correct" if left_count < right_count else "alternate"
    return "tie"


def main() -> None:
    args = _parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    sudachi = dictionary.Dictionary(dict="core").create()

    cases = []
    preference_counts = {"correct": 0, "alternate": 0, "tie": 0}
    for pair in PAIRS:
        correct = _analyze(pair.correct, sudachi)
        alternate = _analyze(pair.alternate, sudachi)
        preference = _preference(correct, alternate)
        preference_counts[preference] += 1
        cases.append(
            {
                "name": pair.name,
                "class": pair.class_name,
                "correct_text": pair.correct,
                "alternate_text": pair.alternate,
                "correct": correct,
                "alternate": alternate,
                "preference": preference,
            }
        )

    payload = {
        "schema_version": 1,
        "preference_counts": preference_counts,
        "cases": cases,
    }
    (output / "linguistic-tiebreak.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        "OCR_LINGUISTIC_TIEBREAK "
        f"cases={len(cases)} correct={preference_counts['correct']} "
        f"alternate={preference_counts['alternate']} ties={preference_counts['tie']}"
    )


if __name__ == "__main__":
    main()
