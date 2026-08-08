"""SudachiPy SplitMode.A tokenizer adapter."""

from __future__ import annotations

from sudachipy import dictionary, tokenizer


class SudachiTokenizer:
    def __init__(self) -> None:
        self._tokenizer = dictionary.Dictionary(dict="core").create()

    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        if not text:
            return ()
        return tuple(
            (
                morpheme.surface(),
                morpheme.dictionary_form(),
                morpheme.reading_form(),
                ",".join(morpheme.part_of_speech()),
            )
            for morpheme in self._tokenizer.tokenize(text, tokenizer.Tokenizer.SplitMode.A)
        )
