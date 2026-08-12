"""Production loading for the reviewed English JMdict gloss pack."""

from __future__ import annotations

from pathlib import Path

from mangasensei.linguistics.jmdict import JsonJmdictDictionary
from mangasensei.linguistics.jmdict_glosses import JmdictGlossPack, JsonJmdictGlossPack
from mangasensei.linguistics.jmdict_packs import resolve_jmdict_pack

_SUPPORTED_PACK_LANGUAGES = frozenset({"en"})


class LazyJmdictGlossPackProvider:
    """Expose only the already-loaded reviewed English JMdict pack."""

    def __init__(
        self,
        configured_english_path: Path,
        english_dictionary: JsonJmdictDictionary,
    ) -> None:
        # Keep the configured path in the constructor contract for compatibility with
        # existing runtime wiring; no optional sibling pack is loaded anymore.
        del configured_english_path
        english_pack = resolve_jmdict_pack("en")
        self._english = JsonJmdictGlossPack.from_reviewed_pack(
            english_pack,
            english_dictionary,
        )

    def is_supported_language(self, language: str) -> bool:
        return language in _SUPPORTED_PACK_LANGUAGES

    def get_pack(self, language: str) -> JmdictGlossPack:
        if language != "en":
            raise LookupError(language)
        return self._english

    @property
    def optional_pack_loaded(self) -> bool:
        """Backward-compatible diagnostic: optional packs no longer exist."""
        return False
