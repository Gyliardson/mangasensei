"""SudachiPy adapter with canonical SplitMode.A tokens and bounded lexical hypotheses."""

from __future__ import annotations

from typing import Any

from sudachipy import dictionary, tokenizer

from mangasensei.linguistics.service import LexicalHypothesis


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
            for morpheme in self._morphemes(text, tokenizer.Tokenizer.SplitMode.A)
        )

    def lexical_hypotheses(
        self,
        text: str,
        *,
        max_span_tokens: int,
    ) -> tuple[LexicalHypothesis, ...]:
        """Derive B/C dictionary-form hypotheses aligned back to canonical A tokens."""

        if not text or max_span_tokens < 2:
            return ()
        canonical = self._morphemes(text, tokenizer.Tokenizer.SplitMode.A)
        if len(canonical) < 2:
            return ()

        start_by_offset = {morpheme.begin(): index for index, morpheme in enumerate(canonical)}
        end_by_offset = {
            morpheme.end(): index + 1 for index, morpheme in enumerate(canonical)
        }
        hypotheses: list[LexicalHypothesis] = []
        seen: set[LexicalHypothesis] = set()
        for mode in (
            tokenizer.Tokenizer.SplitMode.B,
            tokenizer.Tokenizer.SplitMode.C,
        ):
            for morpheme in self._morphemes(text, mode):
                start = start_by_offset.get(morpheme.begin())
                end = end_by_offset.get(morpheme.end())
                if start is None or end is None:
                    continue
                span_length = end - start
                if not 2 <= span_length <= max_span_tokens:
                    continue
                if not _split_matches_canonical(morpheme, canonical[start:end]):
                    continue
                lemma = morpheme.dictionary_form()
                reading = self._dictionary_form_reading(lemma)
                if not lemma or not reading:
                    continue
                hypothesis = LexicalHypothesis(start, end, lemma, reading)
                if hypothesis in seen:
                    continue
                seen.add(hypothesis)
                hypotheses.append(hypothesis)
        hypotheses.sort(
            key=lambda hypothesis: (
                hypothesis.start_token_ordinal,
                -(hypothesis.end_token_ordinal - hypothesis.start_token_ordinal),
                hypothesis.lemma,
                hypothesis.reading,
            )
        )
        return tuple(hypotheses)

    def _morphemes(
        self,
        text: str,
        mode: tokenizer.Tokenizer.SplitMode,
    ) -> tuple[Any, ...]:
        # Sudachi input normalization can expand one source character into multiple
        # morphemes, leaving some morphemes with no span in the original text.
        return tuple(
            morpheme
            for morpheme in self._tokenizer.tokenize(text, mode)
            if morpheme.surface()
        )

    def _dictionary_form_reading(self, lemma: str) -> str:
        return "".join(
            morpheme.reading_form()
            for morpheme in self._morphemes(
                lemma,
                tokenizer.Tokenizer.SplitMode.A,
            )
        )


def _split_matches_canonical(
    morpheme: Any,
    canonical_span: tuple[Any, ...],
) -> bool:
    split = tuple(
        part
        for part in morpheme.split(tokenizer.Tokenizer.SplitMode.A)
        if part.surface()
    )
    if len(split) != len(canonical_span):
        return False
    return all(
        (
            part.surface(),
            part.begin(),
            part.end(),
        )
        == (
            canonical.surface(),
            canonical.begin(),
            canonical.end(),
        )
        for part, canonical in zip(split, canonical_span, strict=True)
    )
