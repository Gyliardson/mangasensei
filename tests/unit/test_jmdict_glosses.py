from __future__ import annotations

import json
from collections.abc import Mapping
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
    JmdictGlossLookup,
    JmdictGlossLookupStatus,
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


class _PackProvider:
    def __init__(
        self,
        packs: Mapping[str, JmdictGlossPack],
        *,
        supported: set[str] | None = None,
    ) -> None:
        self._packs = dict(packs)
        self._supported = frozenset(self._packs if supported is None else supported)
        self.loads: list[str] = []

    def is_supported_language(self, language: str) -> bool:
        return language in self._supported

    def get_pack(self, language: str) -> JmdictGlossPack:
        self.loads.append(language)
        return self._packs[language]


@dataclass(frozen=True, slots=True)
class _GlosslessPack:
    language: str
    source: JmdictGlossSourceReference

    def lookup_identity(self, identity: LexicalFormIdentity) -> JmdictGlossLookup:
        return JmdictGlossLookup(
            identity=identity,
            status=JmdictGlossLookupStatus.GLOSSES_NOT_FOUND,
            meanings=(),
            source=self.source,
        )


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
    *,
    language: str,
) -> JsonJmdictDictionary:
    path = tmp_path / name
    path.write_bytes(
        convert_simplified_jmdict(
            payload,
            version="jmdict-simplified-fixture-20260810",
            language=language,
            source_url=f"https://example.test/{language}.json.zip",
            license_id="CC-BY-SA-4.0",
            attribution="JMdict fixture data",
        )
    )
    return JsonJmdictDictionary(path)


def _fixture_pack(language: str, dictionary: JsonJmdictDictionary) -> JsonJmdictGlossPack:
    return JsonJmdictGlossPack(
        language=language,
        dictionary=dictionary,
        source=JmdictGlossSourceReference(
            dataset="JMdict",
            language=language,
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
    german: JmdictGlossPack | None = None,
) -> tuple[LocalizedJmdictGlossResolver, _PackProvider]:
    packs: dict[str, JmdictGlossPack] = {"en": _fixture_pack("en", english)}
    supported = {"en"}
    if german is not None:
        packs["de"] = german
        supported.add("de")
    provider = _PackProvider(packs, supported=supported)
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


def test_german_hit_uses_complete_localized_set_without_ordinal_english_splicing(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat", "feline"))],
    )
    german_dictionary = _write_dictionary(
        tmp_path,
        "de.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("Katze", "Hauskatze", "Kätzchen"))],
    )
    identity = _unique_identity(english, "猫", "ネコ")
    resolver, provider = _resolver(english, _fixture_pack("de", german_dictionary))

    result = resolver.resolve(identity, requested_dictionary_language="de")

    assert result.identity is identity
    assert result.effective_dictionary_language == "de"
    assert result.meanings == ("Katze", "Hauskatze", "Kätzchen")
    assert "cat" not in result.meanings
    assert result.fallback_used is False
    assert result.fallback_reason is None
    assert result.source.digest_sha256 == german_dictionary.digest.hex()
    assert provider.loads == ["de"]


def test_german_missing_entry_falls_back_to_same_english_identity(tmp_path: Path) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat",))],
    )
    german_dictionary = _write_dictionary(
        tmp_path,
        "de.json",
        [_entry("jmdict-dog", "犬", "いぬ", ("Hund",))],
    )
    identity = _unique_identity(english, "猫", "ネコ")
    resolver, provider = _resolver(english, _fixture_pack("de", german_dictionary))

    result = resolver.resolve(identity, requested_dictionary_language="de")

    assert result.identity is identity
    assert result.effective_dictionary_language == "en"
    assert result.meanings == ("cat",)
    assert result.fallback_used is True
    assert result.fallback_reason is JmdictGlossFallbackReason.REQUESTED_ENTRY_NOT_FOUND
    assert provider.loads == ["de", "en"]


def test_german_entry_without_exact_canonical_form_falls_back_to_english(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-shape", "形", "かたち", ("shape",))],
    )
    german_dictionary = _write_dictionary(
        tmp_path,
        "de.json",
        [_entry("jmdict-shape", "形", "なり", ("Gestalt",))],
    )
    identity = _unique_identity(english, "形", "カタチ")
    resolver, provider = _resolver(english, _fixture_pack("de", german_dictionary))

    result = resolver.resolve(identity, requested_dictionary_language="de")

    assert result.identity is identity
    assert result.effective_dictionary_language == "en"
    assert result.meanings == ("shape",)
    assert result.fallback_used is True
    assert result.fallback_reason is JmdictGlossFallbackReason.REQUESTED_FORM_NOT_FOUND
    assert provider.loads == ["de", "en"]


def test_german_exact_form_without_glosses_uses_explicit_english_fallback(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat",))],
    )
    identity = _unique_identity(english, "猫", "ネコ")
    glossless_source = JmdictGlossSourceReference(
        dataset="JMdict",
        language="de",
        version=english.version,
        digest_sha256="0" * 64,
    )
    resolver, provider = _resolver(
        english,
        _GlosslessPack(language="de", source=glossless_source),
    )

    result = resolver.resolve(identity, requested_dictionary_language="de")

    assert result.effective_dictionary_language == "en"
    assert result.meanings == ("cat",)
    assert result.fallback_used is True
    assert result.fallback_reason is JmdictGlossFallbackReason.REQUESTED_GLOSSES_NOT_FOUND
    assert provider.loads == ["de", "en"]


def test_unsupported_pt_br_is_never_presented_as_native_jmdict_portuguese(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("cat",))],
    )
    identity = _unique_identity(english, "猫", "ネコ")
    resolver, provider = _resolver(english)

    result = resolver.resolve(identity, requested_dictionary_language="pt-BR")

    assert result.requested_dictionary_language == "pt-BR"
    assert result.fallback_dictionary_language == "en"
    assert result.effective_dictionary_language == "en"
    assert result.source.language == "en"
    assert result.meanings == ("cat",)
    assert result.fallback_used is True
    assert result.fallback_reason is JmdictGlossFallbackReason.UNSUPPORTED_REQUESTED_LANGUAGE
    assert provider.loads == ["en"]


def test_issue_26_spelling_reading_applicability_is_preserved_across_localization(
    tmp_path: Path,
) -> None:
    payload = _restricted_multilingual_payload()
    english = _write_converted_dictionary(tmp_path, "en.json", payload, language="eng")
    german = _write_converted_dictionary(tmp_path, "de.json", payload, language="ger")
    identity = _unique_identity(english, "半平", "ハンペイ")
    resolver, _ = _resolver(english, _fixture_pack("de", german))

    result = resolver.resolve(identity, requested_dictionary_language="de")

    assert result.identity is identity
    assert result.meanings == ("gestampfter Fischkuchen",)
    assert "halbe Scheibe" not in result.meanings
    assert result.effective_dictionary_language == "de"


def test_issue_64_script_normalization_preserves_cross_pack_identity(tmp_path: Path) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-kana", "カナ", "カナ", ("kana",))],
    )
    german = _write_dictionary(
        tmp_path,
        "de.json",
        [_entry("jmdict-kana", "カナ", "カナ", ("Kana",))],
    )
    identity = _unique_identity(english, "かな", "カナ")
    resolver, _ = _resolver(english, _fixture_pack("de", german))

    result = resolver.resolve(identity, requested_dictionary_language="de")

    assert identity.form_key == ("かな", "かな")
    assert result.identity is identity
    assert result.meanings == ("Kana",)


def test_issue_112_multi_token_identity_localizes_like_single_token_identity(
    tmp_path: Path,
) -> None:
    english = _write_dictionary(
        tmp_path,
        "en.json",
        [_entry("jmdict-nantoka", "なんとか", "なんとか", ("somehow",))],
    )
    german = _write_dictionary(
        tmp_path,
        "de.json",
        [_entry("jmdict-nantoka", "なんとか", "なんとか", ("irgendwie",))],
    )
    single = LinguisticService(
        _WordTokenizer("なんとか", "なんとか", "ナントカ"),
        english,
    ).analyze("single", "なんとか")
    split = LinguisticService(_SplitNantokaTokenizer(), english).analyze("split", "なんとか")
    single_match = single.lexical_matches[0]
    split_match = next(match for match in split.lexical_matches if match.end_token_ordinal == 3)
    resolver, _ = _resolver(english, _fixture_pack("de", german))

    single_result = resolver.resolve(
        single_match.identity,
        requested_dictionary_language="de",
    )
    split_result = resolver.resolve(
        split_match.identity,
        requested_dictionary_language="de",
    )

    assert single_match.identity == split_match.identity
    assert split_match.start_token_ordinal == 0
    assert split_match.end_token_ordinal == 3
    assert single_result == split_result
    assert split_result.meanings == ("irgendwie",)


def test_ambiguous_lexical_hypothesis_never_reaches_localized_gloss_resolver(
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


def test_reviewed_pack_binding_projects_exact_version_digest_and_language(
    tmp_path: Path,
) -> None:
    german = _write_dictionary(
        tmp_path,
        "de.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("Katze",))],
    )
    reviewed = _reviewed_fixture_pack(tmp_path, "de", "ger", german)

    pack = JsonJmdictGlossPack.from_reviewed_pack(reviewed, german)

    assert pack.language == "de"
    assert pack.source.dataset == "JMdict"
    assert pack.source.version == german.version
    assert pack.source.digest_sha256 == german.digest.hex()


def test_reviewed_pack_binding_rejects_unreviewed_dictionary_digest(tmp_path: Path) -> None:
    german = _write_dictionary(
        tmp_path,
        "de.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("Katze",))],
    )
    reviewed = _reviewed_fixture_pack(tmp_path, "de", "ger", german)
    other = _write_dictionary(
        tmp_path,
        "other-de.json",
        [_entry("jmdict-cat", "猫", "ねこ", ("andere Katze",))],
        version=german.version,
    )

    with pytest.raises(JmdictGlossResolutionError, match="digest"):
        JsonJmdictGlossPack.from_reviewed_pack(reviewed, other)


def test_missing_exact_english_fallback_fails_closed(tmp_path: Path) -> None:
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
        resolver.resolve(identity, requested_dictionary_language="pt-BR")


def _reviewed_fixture_pack(
    tmp_path: Path,
    product_language: str,
    upstream_language: str,
    dictionary: JsonJmdictDictionary,
) -> ResolvedJmdictPack:
    manifest = JmdictManifest.model_validate(
        {
            "version": "fixture-pack-v1",
            "source": {
                "filename": f"jmdict-{upstream_language}.json.zip",
                "url": f"https://example.test/jmdict-{upstream_language}.json.zip",
                "sha256": "1" * 64,
                "size_bytes": 1,
                "max_uncompressed_bytes": 1,
                "language": upstream_language,
                "source_version": dictionary.version,
                "license_id": "CC-BY-SA-4.0",
                "attribution": "JMdict fixture data",
                "redistribution_status": "fixture-only",
            },
            "normalized": {
                "filename": f"jmdict-{product_language}.json",
                "sha256": dictionary.digest.hex(),
                "size_bytes": 1,
                "entry_count": dictionary.entry_count,
                "converter_version": NORMALIZED_CONVERTER_VERSION,
            },
        }
    )
    return ResolvedJmdictPack(
        product_language=product_language,
        upstream_language=upstream_language,
        manifest_path=tmp_path / f"manifest-{product_language}.json",
        manifest=manifest,
        default_language="en",
    )


def _restricted_multilingual_payload() -> dict[str, object]:
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
                    _multilingual_sense(
                        "pounded fish cake",
                        "gestampfter Fischkuchen",
                    ),
                    _multilingual_sense(
                        "half a slice",
                        "halbe Scheibe",
                        applies_to_kanji=["半片"],
                    ),
                ],
            }
        ]
    }


def _multilingual_sense(
    english: str,
    german: str,
    *,
    applies_to_kanji: list[str] | None = None,
) -> dict[str, object]:
    return {
        "gloss": [
            {"lang": "eng", "text": english, "gender": None, "type": None},
            {"lang": "ger", "text": german, "gender": None, "type": None},
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
