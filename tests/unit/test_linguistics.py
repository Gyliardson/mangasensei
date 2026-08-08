from mangasensei.linguistics.service import DictionaryEntry, LinguisticService


class StubTokenizer:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "猫です"
        return (("猫", "猫", "ネコ", "名詞"), ("です", "です", "デス", "助動詞"))


class StubDictionary:
    def lookup(self, lemma: str, reading: str) -> DictionaryEntry | None:
        if lemma == "猫":
            return DictionaryEntry(
                id="jmdict-1467640",
                meanings=("cat",),
                source="JMdict",
                jlpt_level="N5",
                jlpt_official=False,
            )
        return None


def test_linguistics_keeps_unknown_jlpt_as_null() -> None:
    service = LinguisticService(StubTokenizer(), StubDictionary())

    tokens = service.analyze("region-001", "猫です")

    assert tokens[0].dictionary_id == "jmdict-1467640"
    assert tokens[0].jlpt_level == "N5"
    assert tokens[0].jlpt_official is False
    assert tokens[1].dictionary_id is None
    assert tokens[1].jlpt_level is None
