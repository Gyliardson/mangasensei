from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from mangasensei.linguistics import runtime_glosses
from mangasensei.linguistics.jmdict_glosses import (
    JmdictGlossLookup,
    JmdictGlossLookupStatus,
    JmdictGlossSourceReference,
)
from mangasensei.linguistics.runtime_glosses import LazyJmdictGlossPackProvider
from mangasensei.linguistics.service import LexicalFormIdentity


@dataclass(frozen=True, slots=True)
class _Pack:
    language: str
    source: JmdictGlossSourceReference

    def lookup_identity(self, identity: LexicalFormIdentity) -> JmdictGlossLookup:
        return JmdictGlossLookup(
            identity=identity,
            status=JmdictGlossLookupStatus.FOUND,
            meanings=("fixture",),
            source=self.source,
        )


def _pack(language: str) -> _Pack:
    return _Pack(
        language=language,
        source=JmdictGlossSourceReference(
            dataset="JMdict",
            language=language,
            version="fixture",
            digest_sha256="11" * 32,
        ),
    )


def test_default_english_provider_does_not_instantiate_german(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    english = _pack("en")
    verified_languages: list[str] = []

    monkeypatch.setattr(
        runtime_glosses,
        "resolve_jmdict_pack",
        lambda _path, language: SimpleNamespace(product_language=language),
    )
    monkeypatch.setattr(
        runtime_glosses,
        "verify_jmdict_pack",
        lambda _path, language: verified_languages.append(language),
    )
    monkeypatch.setattr(
        runtime_glosses.JsonJmdictGlossPack,
        "from_reviewed_pack",
        lambda _reviewed, _dictionary: english,
    )

    provider = LazyJmdictGlossPackProvider(tmp_path / "jmdict.json", object())

    assert provider.get_pack("en") is english
    assert provider.optional_pack_loaded is False
    assert verified_languages == []
    assert provider.is_supported_language("pt-BR") is False


def test_german_pack_is_verified_loaded_once_and_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    english = _pack("en")
    german = _pack("de")
    verified_languages: list[str] = []
    dictionary_paths: list[Path] = []
    reviewed_en = SimpleNamespace(product_language="en")
    reviewed_de = SimpleNamespace(product_language="de", path=tmp_path / "jmdict-de.json")

    monkeypatch.setattr(runtime_glosses, "resolve_jmdict_pack", lambda _path, _lang: reviewed_en)

    def verify(_path: Path, language: str) -> object:
        verified_languages.append(language)
        return reviewed_de

    monkeypatch.setattr(runtime_glosses, "verify_jmdict_pack", verify)
    monkeypatch.setattr(
        runtime_glosses,
        "JsonJmdictDictionary",
        lambda path: dictionary_paths.append(path) or object(),
    )
    monkeypatch.setattr(
        runtime_glosses.JsonJmdictGlossPack,
        "from_reviewed_pack",
        lambda reviewed, _dictionary: german if reviewed is reviewed_de else english,
    )

    provider = LazyJmdictGlossPackProvider(tmp_path / "jmdict.json", object())

    assert provider.get_pack("de") is german
    assert provider.get_pack("de") is german
    assert provider.optional_pack_loaded is True
    assert verified_languages == ["de"]
    assert dictionary_paths == [reviewed_de.path]
