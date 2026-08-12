from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from mangasensei.linguistics.jmdict import (
    DICTIONARY_NAMESPACE,
    NORMALIZED_CONVERTER_VERSION,
    JsonJmdictDictionary,
)
from mangasensei.linguistics.jmdict_bootstrap import JmdictManifest, convert_simplified_jmdict
from mangasensei.linguistics.jmdict_glosses import (
    JmdictGlossFallbackReason,
    JmdictGlossPack,
    JmdictGlossResolutionError,
    JmdictGlossSourceReference,
    JsonJmdictGlossPack,
    LocalizedJmdictGlossResolver,
)
from mangasensei.linguistics.jmdict_packs import ResolvedJmdictPack
from mangasensei.linguistics.service import (
    DictionaryLookupStatus,
    LexicalFormIdentity,
    LinguisticService,
)


class _EnglishPackProvider:
    def __init__(self, pack: JmdictGlossPack) -> None:
        self._pack = pack
        self.loads: list[str] = []

    def is_supported_language(self, language: str) -> bool:
        return language == "en"

    def get_pack(self, language: str) -> JmdictGlossPack:
        if language != "en":
            raise LookupError(language)
        self.loads.append(language)
        return self._pack


class _WordTokenizer:
    def __init__(self, surface: str, lemma: str, reading: str) -> None:
        self._surface = surface
        self._lemma = lemma
        self._reading = reading

    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == self._surface
        return ((self._surface, self._lemma, self._reading, "名詞"),)


class _SplitNantokaTokenizer:
    def tokenize(self, text: str) -> tuple[tuple[str, str, str, str], ...]:
        assert text == "なんとか"
        return (
            ("なん", "なん", "ナン", "代名詞"),
            ("と", "と", "ト", "助詞"),
            ("か", "か", "カ", "助詞"),
        )


def _entry(
    entry_id: str,
    lemma: str,
    reading: str,
    meanings: tuple[str, ...],
) -> dict[str, object]:
    return {
        "id": entry_id,
        "forms": [
            {
                "lemma": lemma,
                "reading": reading,
                "meanings": list(meanings),
            }
        ],
    }


def _write_dictionary(
    tmp_path: Path,
    name: str,
    entries: list[dict[str, object]],
    *,
    version: str = "jmdict-simplified-fixture-20260810",
) -> JsonJmdictDictionary:
    path = tmp_path / name
    path.write_text(
        json.dumps(
            {
                "converterVersion": NORMALIZED_CONVERTER_VERSION,
                "version": version,
                "entries": entries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return JsonJmdictDictionary(path)


def _write_converted_dictionary(
    tmp_path: Path,
    name: str,
    payload: dict[str, object],
) -> JsonJmdictDictionary:
    path = tmp_path / name
    path.write_bytes(
        convert_simplified_jmdict(
            payload,
            version="jmdict-simplified-fixture-20260810",
            language="eng",
            source_url="https://example.test/eng.json.zip",
            license_id="CC-BY-SA-4.0",
            attribution="JMdict fixture data",
        )
    )
    return JsonJmdictDictionary(path)


def _fixture_pack(dictionary: JsonJmdictDictionary) -> JsonJmdictGlossPack:
    return JsonJmdictGlossPack(
        language="en",
        dictionary=dictionary,
        source=JmdictGlossSourceReference(
            dataset="JMdict",
            language="en",
            version=dictionary.version,
            digest_sha256=dictionary.digest.hex(),
        ),
    )


def _unique_identity(
    dictionary: JsonJmdictDictionary,
    lemma: str,
    reading: str,
) -> LexicalFormIdentity:
    entry = dictionary.lookup_candidates(lemma, reading).unique_entry
    assert entry is not None
    return entry.identity


def _resolver(
    english: JsonJmdictDictionary,
) -> tuple[LocalizedJmdictGlossResolver, _EnglishPackProvider]:
    provider = _EnglishPackProvider(_fixture_pack(english))
    return LocalizedJmdictGlossResolver(provider), provider


def test_exact_identity_lookup_does_not_rerun_ambiguous_candidate_selection(
    tmp_path: Path,
) -> None:
    dictionary = _write_dictionary(
        tmp_path,
        "ambiguous.json",
        [
            _entry("jmdict-bridge", "橋", "はし", ("bridge",)),
            _entry("jmdict-chopsticks", "橋", "はし", ("fixture homograph",)),
        ],
    )

    candidates = dictionary.lookup_candidates("橋", "ハシ")
    assert candidates.status is DictionaryLookupStatus.AMBIGUOUS
    selected = next(
        candidate for candidate in candidates.candidates if candidate.id == "jmdict-bridge"
    )

    assert dictionary.lookup_identity(selected.identity) == selected


def test_english_request_uses_exact_english_identity_without_fallback(tmp_path: Path) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat", "domestic cat"))],
    )
    identity = _unique_identity(english, "猫", "ネコ")
    resolver, provider = _resolver(english)

    result = resolver.resolve(identity, requested_dictionary_language="en")

    assert result.identity is identity
    assert result.requested_dictionary_language == "en"
    assert result.fallback_dictionary_language == "en"
    assert result.effective_dictionary_language == "en"
    assert result.meanings == ("cat", "domestic cat")
    assert result.fallback_used is False
    assert result.fallback_reason is None
    assert result.source.version == english.version
    assert result.source.digest_sha256 == english.digest.hex()
    assert result.source.compact_ref.startswith(f"jmdict:en:{english.version}:")
    assert provider.loads == ["en"]


@pytest.mark.parametrize("historical_language", ["de", "pt-BR"])
def test_historical_unsupported_language_metadata_falls_back_without_loading_retired_pack(
    tmp_path: Path,
    historical_language: str,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat",))],
    )
    identity = _unique_identity(english, "猫", "ネコ")
    resolver, provider = _resolver(english)

    result = resolver.resolve(
        identity,
        requested_dictionary_language=historical_language,
    )

    assert result.requested_dictionary_language == historical_language
    assert result.fallback_dictionary_language == "en"
    assert result.effective_dictionary_language == "en"
    assert result.source.language == "en"
    assert result.meanings == ("cat",)
    assert result.fallback_used is True
    assert result.fallback_reason is JmdictGlossFallbackReason.UNSUPPORTED_REQUESTED_LANGUAGE
    assert provider.loads == ["en"]


def test_issue_26_spelling_reading_applicability_is_preserved_in_english_pack(
    tmp_path: Path,
) -> None:
    english = _write_converted_dictionary(
        tmp_path,
        "en.json",
        _restricted_english_payload(),
    )
    identity = _unique_identity(english, "半平", "ハンペイ")
    resolver, _ = _resolver(english)

    result = resolver.resolve(identity, requested_dictionary_language="en")

    assert result.identity is identity
    assert result.meanings == ("pounded fish cake",)
    assert "half a slice" not in result.meanings
    assert result.effective_dictionary_language == "en"


def test_issue_64_script_normalization_preserves_english_identity(tmp_path: Path) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-kana", "カナ", "カナ", ("kana",))],
    )
    identity = _unique_identity(english, "かな", "カナ")
    resolver, _ = _resolver(english)

    result = resolver.resolve(identity, requested_dictionary_language="en")

    assert identity.form_key == ("かな", "かな")
    assert result.identity is identity
    assert result.meanings == ("kana",)


def test_issue_112_multi_token_identity_matches_single_token_identity_in_english(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-nantoka", "なんとか", "なんとか", ("somehow",))],
    )
    single = LinguisticService(
        _WordTokenizer("なんとか", "なんとか", "ナントカ"),
        english,
    ).analyze("single", "なんとか")
    split = LinguisticService(_SplitNantokaTokenizer(), english).analyze("split", "なんとか")
    single_match = single.lexical_matches[0]
    split_match = next(match for match in split.lexical_matches if match.end_token_ordinal == 3)
    resolver, _ = _resolver(english)

    single_result = resolver.resolve(
        single_match.identity,
        requested_dictionary_language="en",
    )
    split_result = resolver.resolve(
        split_match.identity,
        requested_dictionary_language="en",
    )

    assert single_match.identity == split_match.identity
    assert split_match.start_token_ordinal == 0
    assert split_match.end_token_ordinal == 3
    assert single_result == split_result
    assert split_result.meanings == ("somehow",)


def test_ambiguous_lexical_hypothesis_never_reaches_gloss_resolver(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [
            _entry("jmdict-ano-1", "あの", "あの", ("that",)),
            _entry("jmdict-ano-2", "あの", "あの", ("well",)),
        ],
    )
    analysis = LinguisticService(
        _WordTokenizer("あの", "あの", "アノ"),
        english,
    ).analyze("ambiguous", "あの")
    resolver, provider = _resolver(english)

    localized = tuple(
        resolver.resolve(match.identity, requested_dictionary_language="en")
        for match in analysis.lexical_matches
    )

    assert analysis.lexical_matches == ()
    assert localized == ()
    assert provider.loads == []


def test_english_only_lexical_behavior_remains_backward_compatible(tmp_path: Path) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat", "domestic cat"))],
    )
    analysis = LinguisticService(
        _WordTokenizer("猫", "猫", "ネコ"),
        english,
    ).analyze("legacy", "猫")
    legacy_match = analysis.lexical_matches[0]
    resolver, _ = _resolver(english)

    localized = resolver.resolve(
        legacy_match.identity,
        requested_dictionary_language="en",
    )

    assert legacy_match.meanings == ("cat", "domestic cat")
    assert legacy_match.source == f"JMdict {english.version}"
    assert localized.identity == legacy_match.identity
    assert localized.meanings == legacy_match.meanings


def test_reviewed_english_pack_binding_projects_exact_version_digest_and_language(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat",))],
    )
    reviewed = _reviewed_fixture_pack(tmp_path, english)

    pack = JsonJmdictGlossPack.from_reviewed_pack(reviewed, english)

    assert pack.language == "en"
    assert pack.source.dataset == "JMdict"
    assert pack.source.version == english.version
    assert pack.source.digest_sha256 == english.digest.hex()


def test_reviewed_english_pack_binding_rejects_unreviewed_dictionary_digest(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat",))],
    )
    reviewed = _reviewed_fixture_pack(tmp_path, english)
    other = _write_dictionary(
        tmp_path,
        "other-en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("other cat",))],
        version=english.version,
    )

    with pytest.raises(JmdictGlossResolutionError, match="digest"):
        JsonJmdictGlossPack.from_reviewed_pack(reviewed, other)


def test_missing_exact_english_fallback_for_historical_metadata_fails_closed(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-dog", "犬", "いぬ", ("dog",))],
    )
    identity = LexicalFormIdentity(
        dictionary_namespace=DICTIONARY_NAMESPACE,
        entry_id="jmdict-cat",
        lemma="猫",
        reading="ねこ",
    )
    resolver, _ = _resolver(english)

    with pytest.raises(JmdictGlossResolutionError, match="mandatory English"):
        resolver.resolve(identity, requested_dictionary_language="de")


def _reviewed_fixture_pack(
    tmp_path: Path,
    dictionary: JsonJmdictDictionary,
) -> ResolvedJmdictPack:
    manifest = JmdictManifest.model_validate(
        {
            "version": "fixture-pack-v1",
            "source": {
                "filename": "jmdict-eng.json.zip",
                "url": "https://example.test/jmdict-eng.json.zip",
                "sha256": "1" * 64,
                "size_bytes": 1,
                "max_uncompressed_bytes": 1,
                "language": "eng",
                "source_version": dictionary.version,
                "license_id": "CC-BY-SA-4.0",
                "attribution": "JMdict fixture data",
                "redistribution_status": "fixture-only",
            },
            "normalized": {
                "filename": "jmdict-en.json",
                "sha256": dictionary.digest.hex(),
                "size_bytes": 1,
                "entry_count": dictionary.entry_count,
                "converter_version": NORMALIZED_CONVERTER_VERSION,
            },
        }
    )
    return ResolvedJmdictPack(
        product_language="en",
        upstream_language="eng",
        manifest_path=tmp_path / "manifest-en.json",
        manifest=manifest,
        default_language="en",
    )


def _restricted_english_payload() -> dict[str, object]:
    return {
        "words": [
            {
                "id": "1010230",
                "kanji": [
                    {"text": "半片", "common": False, "tags": []},
                    {"text": "半平", "common": False, "tags": []},
                ],
                "kana": [
                    {
                        "text": "はんぺん",
                        "common": True,
                        "tags": [],
                        "appliesToKanji": ["*"],
                    },
                    {
                        "text": "はんぺい",
                        "common": False,
                        "tags": [],
                        "appliesToKanji": ["半平"],
                    },
                ],
                "sense": [
                    _english_sense("pounded fish cake"),
                    _english_sense(
                        "half a slice",
                        applies_to_kanji=["半片"],
                    ),
                ],
            }
        ]
    }


def _english_sense(
    english: str,
    *,
    applies_to_kanji: list[str] | None = None,
) -> dict[str, object]:
    return {
        "gloss": [
            {"lang": "eng", "text": english, "gender": None, "type": None},
        ],
        "appliesToKanji": applies_to_kanji or ["*"],
        "appliesToKana": ["*"],
        "dialect": [],
        "field": [],
        "info": [],
        "languageSource": [],
        "misc": [],
        "partOfSpeech": ["n"],
        "related": [],
        "antonym": [],
    }
