from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sudachipy import dictionary, tokenizer

from mangasensei.linguistics.jmdict import NORMALIZED_CONVERTER_VERSION, JsonJmdictDictionary
from mangasensei.linguistics.service import (
    MAX_LEXICAL_SPAN_TOKENS,
    DictionaryEntry,
    DictionaryLookupResult,
    DictionaryLookupStatus,
    LexicalFormIdentity,
    LexicalHypothesis,
    LinguisticService,
)
from mangasensei.linguistics.sudachi import SudachiTokenizer


def _entry(entry_id: str, lemma: str, reading: str, meaning: str) -> dict[str, object]:
    return {
        "id": entry_id,
        "forms": [
            {
                "lemma": lemma,
                "reading": reading,
                "meanings": [meaning],
            }
        ],
    }


def _reviewed_dictionary(tmp_path: Path) -> JsonJmdictDictionary:
    path = tmp_path / "jmdict.json"
    path.write_text(
        json.dumps(
            {
                "converterVersion": NORMALIZED_CONVERTER_VERSION,
                "version": "issue-103-slice-2-fixture",
                "entries": [
                    _entry("jmdict-1008460", "でも", "でも", "but"),
                    _entry("jmdict-1084000", "でも", "でも", "even"),
                    _entry("jmdict-1188420", "なんとか", "なんとか", "somehow"),
                    _entry("jmdict-2200100", "上では", "うえでは", "as far as ... is concerned"),
                    _entry(
                        "jmdict-tsukiau-fixture",
                        "付き合う",
                        "つきあう",
                        "to associate with",
                    ),
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return JsonJmdictDictionary(path)


class _RecordingDictionary:
    def __init__(self, inner: JsonJmdictDictionary) -> None:
        self._inner = inner
        self.version = inner.version
        self.digest = inner.digest
        self.calls: list[tuple[str, str]] = []

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        self.calls.append((lemma, reading))
        return self._inner.lookup_candidates(lemma, reading)


def _describe(morpheme: Any) -> tuple[str, str, str, int, int]:
    return (
        morpheme.surface(),
        morpheme.dictionary_form(),
        morpheme.reading_form(),
        morpheme.begin(),
        morpheme.end(),
    )


def test_current_sudachi_compound_verb_evidence_is_pinned() -> None:
    sudachi = dictionary.Dictionary(dict="core").create()
    text = "付き合っ"

    a = tuple(sudachi.tokenize(text, tokenizer.Tokenizer.SplitMode.A))
    b = tuple(sudachi.tokenize(text, tokenizer.Tokenizer.SplitMode.B))
    c = tuple(sudachi.tokenize(text, tokenizer.Tokenizer.SplitMode.C))

    assert tuple(_describe(morpheme) for morpheme in a) == (
        ("付き", "付く", "ツキ", 0, 2),
        ("合っ", "合う", "アッ", 2, 4),
    )
    assert tuple(
        tuple(_describe(part) for part in morpheme.split(tokenizer.Tokenizer.SplitMode.A))
        for morpheme in a
    ) == ((), ())
    expected_coarse = (("付き合っ", "付き合う", "ツキアッ", 0, 4),)
    expected_split = (
        (
            ("付き", "付く", "ツキ", 0, 2),
            ("合っ", "合う", "アッ", 2, 4),
        ),
    )
    assert tuple(_describe(morpheme) for morpheme in b) == expected_coarse
    assert tuple(_describe(morpheme) for morpheme in c) == expected_coarse
    assert tuple(
        tuple(_describe(part) for part in morpheme.split(tokenizer.Tokenizer.SplitMode.A))
        for morpheme in b
    ) == expected_split
    assert tuple(
        tuple(_describe(part) for part in morpheme.split(tokenizer.Tokenizer.SplitMode.A))
        for morpheme in c
    ) == expected_split


def test_sudachi_coarse_hypothesis_uses_dictionary_form_reading() -> None:
    hypotheses = SudachiTokenizer().lexical_hypotheses(
        "付き合っ",
        max_span_tokens=MAX_LEXICAL_SPAN_TOKENS,
    )

    assert LexicalHypothesis(0, 2, "付き合う", "ツキアウ") in hypotheses


def test_demo_span_is_attempted_but_remains_ambiguous(tmp_path: Path) -> None:
    dictionary = _RecordingDictionary(_reviewed_dictionary(tmp_path))
    sudachi = SudachiTokenizer()

    direct = dictionary.lookup_candidates("でも", "デモ")
    analysis = LinguisticService(sudachi, dictionary).analyze("region-demo", "でも")

    assert tuple(token.surface for token in analysis.tokens) == ("で", "も")
    assert direct.status is DictionaryLookupStatus.AMBIGUOUS
    assert tuple(candidate.id for candidate in direct.candidates) == (
        "jmdict-1008460",
        "jmdict-1084000",
    )
    assert dictionary.calls.count(("でも", "デモ")) == 2
    assert all(match.surface != "でも" for match in analysis.lexical_matches)


def test_nantoka_resolves_through_general_surface_span(tmp_path: Path) -> None:
    analysis = LinguisticService(
        SudachiTokenizer(),
        _reviewed_dictionary(tmp_path),
    ).analyze("region-nantoka", "なんとか")

    assert tuple(token.surface for token in analysis.tokens) == ("なん", "と", "か")
    match = next(match for match in analysis.lexical_matches if match.surface == "なんとか")
    assert (match.start_token_ordinal, match.end_token_ordinal) == (0, 3)
    assert match.identity.entry_id == "jmdict-1188420"


def test_ue_dewa_resolves_through_general_surface_span(tmp_path: Path) -> None:
    analysis = LinguisticService(
        SudachiTokenizer(),
        _reviewed_dictionary(tmp_path),
    ).analyze("region-ue-dewa", "上では")

    assert tuple(token.surface for token in analysis.tokens) == ("上", "で", "は")
    match = next(match for match in analysis.lexical_matches if match.surface == "上では")
    assert (match.start_token_ordinal, match.end_token_ordinal) == (0, 3)
    assert match.identity.entry_id == "jmdict-2200100"


def test_tsukiatte_uses_general_aligned_sudachi_dictionary_form(tmp_path: Path) -> None:
    analysis = LinguisticService(
        SudachiTokenizer(),
        _reviewed_dictionary(tmp_path),
    ).analyze("region-tsukiau", "付き合っ")

    assert tuple(
        (token.surface, token.lemma, token.reading)
        for token in analysis.tokens
    ) == (
        ("付き", "付く", "ツキ"),
        ("合っ", "合う", "アッ"),
    )
    match = next(match for match in analysis.lexical_matches if match.surface == "付き合っ")
    assert (match.start_token_ordinal, match.end_token_ordinal) == (0, 2)
    assert match.display_lemma == "付き合う"
    assert match.display_reading == "ツキアウ"
    assert match.identity.lemma == "付き合う"


class _SequenceTokenizer:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "甲乙丙"
        return (
            ("甲", "甲", "コウ", "名詞"),
            ("乙", "乙", "オツ", "名詞"),
            ("丙", "丙", "ヘイ", "名詞"),
        )


class _OverlapDictionary:
    _READINGS = {
        "甲": "こう",
        "甲乙": "こうおつ",
        "甲乙丙": "こうおつへい",
        "乙丙": "おつへい",
        "丙": "へい",
    }

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        del reading
        normalized_reading = self._READINGS.get(lemma)
        if normalized_reading is None:
            return DictionaryLookupResult.from_candidates(())
        entry = DictionaryEntry(
            identity=LexicalFormIdentity(
                "JMdict",
                f"fixture-{lemma}",
                lemma,
                normalized_reading,
            ),
            meanings=(lemma,),
            source="overlap fixture",
            jlpt_level=None,
            jlpt_official=False,
        )
        return DictionaryLookupResult.from_candidates((entry,))


def test_overlapping_unique_matches_coexist_in_deterministic_order() -> None:
    analysis = LinguisticService(
        _SequenceTokenizer(),
        _OverlapDictionary(),
    ).analyze("region-overlap", "甲乙丙")

    assert tuple(match.surface for match in analysis.lexical_matches) == (
        "甲乙丙",
        "甲乙",
        "甲",
        "乙丙",
        "丙",
    )


class _DuplicateHypothesisTokenizer:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "甲乙"
        return (
            ("甲", "甲", "コウ", "名詞"),
            ("乙", "乙", "オツ", "名詞"),
        )

    def lexical_hypotheses(
        self,
        text: str,
        *,
        max_span_tokens: int,
    ) -> tuple[LexicalHypothesis, ...]:
        assert text == "甲乙"
        assert max_span_tokens == MAX_LEXICAL_SPAN_TOKENS
        return (
            LexicalHypothesis(0, 2, "甲乙", "コウオツ"),
            LexicalHypothesis(0, 2, "甲乙", "コウオツ"),
        )


class _RecordingOverlapDictionary(_OverlapDictionary):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        self.calls.append((lemma, reading))
        return super().lookup_candidates(lemma, reading)


def test_duplicate_hypothesis_strategies_do_not_duplicate_lookup_or_occurrence() -> None:
    dictionary = _RecordingOverlapDictionary()

    analysis = LinguisticService(
        _DuplicateHypothesisTokenizer(),
        dictionary,
    ).analyze("region-duplicate", "甲乙")

    assert dictionary.calls.count(("甲乙", "コウオツ")) == 1
    assert tuple(match.surface for match in analysis.lexical_matches) == ("甲乙", "甲")


class _LongRegionTokenizer:
    def __init__(self, token_count: int) -> None:
        self._token_count = token_count

    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "long-region"
        return tuple(
            (f"語{index}", f"語{index}", f"ゴ{index}", "名詞")
            for index in range(self._token_count)
        )


class _NoMatchRecordingDictionary:
    version = "bounded-fixture"
    digest = hashlib.sha256(version.encode()).digest()

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        self.calls.append((lemma, reading))
        return DictionaryLookupResult.from_candidates(())


def test_lexical_acquisition_work_is_bounded_for_long_regions() -> None:
    assert MAX_LEXICAL_SPAN_TOKENS == 4
    for token_count, expected_calls in ((20, 74), (100, 394)):
        dictionary = _NoMatchRecordingDictionary()

        analysis = LinguisticService(
            _LongRegionTokenizer(token_count),
            dictionary,
        ).analyze("region-long", "long-region")

        assert len(analysis.tokens) == token_count
        assert analysis.lexical_matches == ()
        assert len(dictionary.calls) == expected_calls
