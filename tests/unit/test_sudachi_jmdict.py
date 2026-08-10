from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mangasensei.linguistics.jmdict import (
    NORMALIZED_CONVERTER_VERSION,
    JsonJmdictDictionary,
)
from mangasensei.linguistics.service import DictionaryLookupStatus, LexicalFormIdentity
from mangasensei.linguistics.sudachi import SudachiTokenizer


def test_sudachi_uses_dictionary_form_reading_and_split_mode_a() -> None:
    tokens = SudachiTokenizer().tokenize("猫でした")

    assert tokens[0][:3] == ("猫", "猫", "ネコ")
    assert tokens[0][3].startswith("名詞")
    assert "です" in tuple(token[1] for token in tokens)


def test_sudachi_drops_zero_width_morphemes_from_input_normalization() -> None:
    tokens = SudachiTokenizer().tokenize("㈱")

    assert tokens
    assert all(token[0] for token in tokens)
    assert "".join(token[0] for token in tokens) == "㈱"


def test_jmdict_lookup_distinguishes_not_found_unique_and_ambiguous(tmp_path: Path) -> None:
    dictionary_path = tmp_path / "jmdict.json"
    dictionary_path.write_text(
        json.dumps(
            {
                "converterVersion": NORMALIZED_CONVERTER_VERSION,
                "version": "test",
                "entries": [
                    {
                        "id": "jmdict-2000000",
                        "forms": [{"lemma": "あの", "reading": "あの", "meanings": ["that"]}],
                    },
                    {
                        "id": "jmdict-1000000",
                        "forms": [
                            {"lemma": "あの", "reading": "あの", "meanings": ["well"]}
                        ],
                    },
                    {
                        "id": "jmdict-1467640",
                        "forms": [
                            {"lemma": "猫", "reading": "ねこ", "meanings": ["cat"]}
                        ],
                        "jlptLevel": "N5",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dictionary = JsonJmdictDictionary(dictionary_path)

    missing = dictionary.lookup_candidates("犬", "イヌ")
    unique = dictionary.lookup_candidates("猫", "ネコ")
    ambiguous = dictionary.lookup_candidates("あの", "アノ")

    assert dictionary.version == "test"
    assert dictionary.entry_count == 3
    assert dictionary.digest == hashlib.sha256(dictionary_path.read_bytes()).digest()
    assert missing.status is DictionaryLookupStatus.NOT_FOUND
    assert missing.candidates == ()
    assert missing.unique_entry is None
    assert unique.status is DictionaryLookupStatus.UNIQUE
    assert unique.unique_entry is not None
    assert unique.unique_entry.identity == LexicalFormIdentity(
        dictionary_namespace="JMdict",
        entry_id="jmdict-1467640",
        lemma="猫",
        reading="ねこ",
    )
    assert unique.unique_entry.jlpt_level == "N5"
    assert ambiguous.status is DictionaryLookupStatus.AMBIGUOUS
    assert tuple(candidate.id for candidate in ambiguous.candidates) == (
        "jmdict-1000000",
        "jmdict-2000000",
    )
    assert ambiguous.unique_entry is None


def test_jmdict_identity_comes_from_canonical_v3_form_key(tmp_path: Path) -> None:
    dictionary_path = tmp_path / "jmdict.json"
    dictionary_path.write_text(
        json.dumps(
            {
                "converterVersion": NORMALIZED_CONVERTER_VERSION,
                "version": "test",
                "entries": [
                    {
                        "id": "jmdict-kana",
                        "forms": [
                            {
                                "lemma": "カタカナ",
                                "reading": "カタカナ",
                                "meanings": ["katakana"],
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dictionary = JsonJmdictDictionary(dictionary_path)

    result = dictionary.lookup_candidates("かたかな", "カタカナ")

    assert result.unique_entry is not None
    assert result.unique_entry.identity.form_key == ("かたかな", "かたかな")
