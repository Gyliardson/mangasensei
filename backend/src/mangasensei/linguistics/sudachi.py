"""SudachiPy SplitMode.A tokenizer adapter."""

from __future__ import annotations

from sudachipy import dictionary, tokenizer


class SudachiTokenizer:
    def __init__(self) -> None:
        self._tokenizer = dictionary.Dictionary(dict="core").create()

    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        if not text:
            return ()
        analyzed: list[tuple[str, str, str, str]] = []
        for morpheme in self._tokenizer.tokenize(text, tokenizer.Tokenizer.SplitMode.A):
            surface = morpheme.surface()
            # Sudachi input normalization can expand one source character into multiple
            # morphemes, leaving some morphemes with no span in the original text.
            if not surface:
                continue
            analyzed.append(
                (
                    surface,
                    morpheme.dictionary_form(),
                    morpheme.reading_form(),
                    ",".join(morpheme.part_of_speech()),
                )
            )
        return tuple(analyzed)
