from mangasensei.linguistics.service import (
    DictionaryEntry,
    DictionaryLookupResult,
    LexicalFormIdentity,
    LinguisticService,
)


class StubTokenizer:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "猫です"
        return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))


class StubDictionary:
    def lookup_candidates(self, lemma: str, reading: str) -> DictionaryLookupResult:
        if lemma == "猫":
            return DictionaryLookupResult.from_candidates(
                (
                    DictionaryEntry(
                        identity=LexicalFormIdentity(
                            dictionary_namespace="JMdict",
                            entry_id="jmdict-1467640",
                            lemma="猫",
                            reading="ねこ",
                        ),
                        meanings=("cat",),
                        source="JMdict",
                        jlpt_level="N5",
                        jlpt_official=False,
                    ),
                )
            )
        return DictionaryLookupResult.from_candidates(())


def test_linguistics_keeps_tokens_morphological_and_emits_resolved_lexical_match() -> None:
    service = LinguisticService(StubTokenizer(), StubDictionary())

    analysis = service.analyze("region-001", "猫です")

    assert analysis.tokens[0].lemma == "猫"
    assert analysis.tokens[0].reading == "ネコ"
    assert analysis.tokens[1].lemma == "です"
    assert len(analysis.lexical_matches) == 1
    match = analysis.lexical_matches[0]
    assert match.identity == LexicalFormIdentity(
        dictionary_namespace="JMdict",
        entry_id="jmdict-1467640",
        lemma="猫",
        reading="ねこ",
    )
    assert match.start_token_ordinal == 0
    assert match.end_token_ordinal == 1
    assert match.display_reading == "ネコ"
    assert match.jlpt_level == "N5"
    assert match.jlpt_official is False
