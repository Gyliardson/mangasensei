from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mangasensei.linguistics.jmdict import (
    NORMALIZED_CONVERTER_VERSION,
    JsonJmdictDictionary,
)
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


def test_jmdict_lookup_requires_unambiguous_lemma_and_reading(tmp_path: Path) -> None:
    dictionary_path = tmp_path / "jmdict.json"
    dictionary_path.write_text(
        json.dumps(
            {
                "converterVersion": NORMALIZED_CONVERTER_VERSION,
                "version": "test",
                "entries": [
                    {
                        "id": "jmdict-1467640",
                        "forms": [
                            {
                                "lemma": "猫",
                                "reading": "ねこ",
                                "meanings": ["cat"],
                            }
                        ],
                        "jlptLevel": "N5",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dictionary = JsonJmdictDictionary(dictionary_path)

    entry = dictionary.lookup("猫", "ネコ")

    assert dictionary.version == "test"
    assert dictionary.entry_count == 1
    assert dictionary.digest == hashlib.sha256(dictionary_path.read_bytes()).digest()
    assert entry is not None
    assert entry.id == "jmdict-1467640"
    assert entry.jlpt_level == "N5"
    assert entry.jlpt_official is False
    assert dictionary.lookup("犬", "イヌ") is None
