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


def test_runtime_provider_exposes_only_reviewed_english_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    english = _pack("en")
    resolved_languages: list[str] = []

    def resolve(language: str) -> object:
        resolved_languages.append(language)
        return SimpleNamespace(product_language=language)

    monkeypatch.setattr(runtime_glosses, "resolve_jmdict_pack", resolve)
    monkeypatch.setattr(
        runtime_glosses.JsonJmdictGlossPack,
        "from_reviewed_pack",
        lambda _reviewed, _dictionary: english,
    )

    provider = LazyJmdictGlossPackProvider(tmp_path / "jmdict.json", object())

    assert provider.get_pack("en") is english
    assert provider.is_supported_language("en") is True
    assert provider.is_supported_language("de") is False
    assert provider.is_supported_language("pt-BR") is False
    assert provider.optional_pack_loaded is False
    assert resolved_languages == ["en"]

    with pytest.raises(LookupError):
        provider.get_pack("de")
