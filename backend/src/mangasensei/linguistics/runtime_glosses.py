"""Bounded production loading for localized JMdict gloss packs."""

from __future__ import annotations

from pathlib import Path

from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.jmdict_glosses import JmdictGlossPack, JsonJmdictGlossPack
from mangasensei.linguistics.jmdict_packs import resolve_jmdict_pack, verify_jmdict_pack

_SUPPORTED_PACK_LANGUAGES = frozenset({"en", "de"})


class LazyJmdictGlossPackProvider:
    """Reuse English and load at most one reviewed non-English pack on demand."""

    def __init__(
        self,
        configured_english_path: Path,
        english_dictionary: JsonJmdictDictionary,
    ) -> None:
        self._configured_english_path = configured_english_path
        english_pack = resolve_jmdict_pack(configured_english_path, "en")
        self._english = JsonJmdictGlossPack.from_reviewed_pack(
            english_pack,
            english_dictionary,
        )
        self._optional: JsonJmdictGlossPack | None = None

    def is_supported_language(self, language: str) -> bool:
        return language in _SUPPORTED_PACK_LANGUAGES

    def get_pack(self, language: str) -> JmdictGlossPack:
        if language == "en":
            return self._english
        if language != "de":
            raise LookupError(language)
        if self._optional is None:
            reviewed = verify_jmdict_pack(self._configured_english_path, "de")
            dictionary = JsonJmdictDictionary(reviewed.path)
            self._optional = JsonJmdictGlossPack.from_reviewed_pack(reviewed, dictionary)
        return self._optional

    @property
    def optional_pack_loaded(self) -> bool:
        """Expose bounded-load state for diagnostics and regression tests."""
        return self._optional is not None
