from __future__ import annotations

from sudachipy import dictionary, tokenizer


def _describe(morpheme: object) -> tuple[object, ...]:
    return (
        morpheme.surface(),
        morpheme.dictionary_form(),
        morpheme.reading_form(),
        morpheme.begin(),
        morpheme.end(),
    )


def test_slice2_probe_current_sudachi_behavior() -> None:
    sudachi = dictionary.Dictionary(dict="core").create()
    text = "付き合っ"
    evidence: dict[str, object] = {}
    for name, mode in (
        ("A", tokenizer.Tokenizer.SplitMode.A),
        ("B", tokenizer.Tokenizer.SplitMode.B),
        ("C", tokenizer.Tokenizer.SplitMode.C),
    ):
        morphemes = tuple(sudachi.tokenize(text, mode))
        evidence[name] = tuple(_describe(morpheme) for morpheme in morphemes)
        evidence[f"{name}->A"] = tuple(
            tuple(_describe(part) for part in morpheme.split(tokenizer.Tokenizer.SplitMode.A))
            for morpheme in morphemes
        )
    raise AssertionError(repr(evidence))
