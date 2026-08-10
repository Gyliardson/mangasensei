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
    resolved_languages: list[str] = []
    verified_languages: list[str] = []

    def resolve(language: str) -> object:
        resolved_languages.append(language)
        return SimpleNamespace(product_language=language)

    monkeypatch.setattr(runtime_glosses, "resolve_jmdict_pack", resolve)

    def verify(_path: Path, *, language: str = "en", registry_path: Path | None = None) -> Path:
        del registry_path
        verified_languages.append(language)
        return tmp_path / f"jmdict-{language}.json"

    monkeypatch.setattr(runtime_glosses, "verify_jmdict_pack", verify)
    monkeypatch.setattr(
        runtime_glosses.JsonJmdictGlossPack,
        "from_reviewed_pack",
        lambda _reviewed, _dictionary: english,
    )

    provider = LazyJmdictGlossPackProvider(tmp_path / "jmdict.json", object())

    assert provider.get_pack("en") is english
    assert provider.optional_pack_loaded is False
    assert resolved_languages == ["en"]
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
    reviewed_de = SimpleNamespace(product_language="de")
    verified_de_path = tmp_path / "jmdict-de.json"

    def resolve(language: str) -> object:
        return reviewed_de if language == "de" else reviewed_en

    monkeypatch.setattr(runtime_glosses, "resolve_jmdict_pack", resolve)

    def verify(_path: Path, *, language: str = "en", registry_path: Path | None = None) -> Path:
        del registry_path
        verified_languages.append(language)
        return verified_de_path

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
    assert dictionary_paths == [verified_de_path]
