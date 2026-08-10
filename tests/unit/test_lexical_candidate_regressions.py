from __future__ import annotations

import json
from pathlib import Path

from mangasensei.linguistics.jmdict import NORMALIZED_CONVERTER_VERSION, JsonJmdictDictionary
from mangasensei.linguistics.service import DictionaryLookupStatus


def _entry(entry_id: str, lemma: str, reading: str, meaning: str) -> dict[str, object]:
    return {
        "id": entry_id,
        "forms": [{"lemma": lemma, "reading": reading, "meanings": [meaning]}],
    }


def test_issue_103_measured_single_token_cardinalities_are_explicit(tmp_path: Path) -> None:
    entries: list[dict[str, object]] = [
        _entry("jmdict-1012810", "やっぱり", "やっぱり", "as expected"),
        _entry("jmdict-1582670", "二人", "ふたり", "two people"),
        _entry("jmdict-1352130", "上", "うえ", "above"),
        _entry("jmdict-an-1", "あの", "あの", "that"),
        _entry("jmdict-an-2", "あの", "あの", "well"),
    ]
    entries.extend(
        _entry(f"jmdict-ga-{index:02d}", "が", "が", f"ga candidate {index}")
        for index in range(1, 10)
    )
    path = tmp_path / "jmdict.json"
    path.write_text(
        json.dumps(
            {
                "converterVersion": NORMALIZED_CONVERTER_VERSION,
                "version": "issue-103-cardinality-fixture",
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    dictionary = JsonJmdictDictionary(path)

    controls = (
        dictionary.lookup_candidates("やっぱり", "ヤッパリ"),
        dictionary.lookup_candidates("二人", "フタリ"),
        dictionary.lookup_candidates("上", "ウエ"),
    )
    for result in controls:
        assert result.status is DictionaryLookupStatus.UNIQUE
        assert len(result.candidates) == 1
        assert result.unique_entry is result.candidates[0]

    ano = dictionary.lookup_candidates("あの", "アノ")
    ga = dictionary.lookup_candidates("が", "ガ")

    assert ano.status is DictionaryLookupStatus.AMBIGUOUS
    assert len(ano.candidates) == 2
    assert ano.unique_entry is None
    assert ga.status is DictionaryLookupStatus.AMBIGUOUS
    assert len(ga.candidates) == 9
    assert ga.unique_entry is None

    assert tuple(candidate.id for candidate in ga.candidates) == tuple(
        f"jmdict-ga-{index:02d}" for index in range(1, 10)
    )
