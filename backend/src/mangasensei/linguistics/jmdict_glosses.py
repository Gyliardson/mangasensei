"""Language-aware JMdict gloss projection over canonical lexical identities."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from mangasensei.linguistics.jmdict import DICTIONARY_NAMESPACE, JsonJmdictDictionary
from mangasensei.linguistics.jmdict_packs import (
    FALLBACK_DICTIONARY_LANGUAGE,
    ResolvedJmdictPack,
)
from mangasensei.linguistics.service import LexicalFormIdentity

_DATASET = "JMdict"


class JmdictGlossLookupStatus(StrEnum):
    FOUND = "found"
    ENTRY_NOT_FOUND = "entry_not_found"
    FORM_NOT_FOUND = "form_not_found"
    GLOSSES_NOT_FOUND = "glosses_not_found"


class JmdictGlossFallbackReason(StrEnum):
    UNSUPPORTED_REQUESTED_LANGUAGE = "unsupported_requested_language"
    REQUESTED_ENTRY_NOT_FOUND = "requested_entry_not_found"
    REQUESTED_FORM_NOT_FOUND = "requested_form_not_found"
    REQUESTED_GLOSSES_NOT_FOUND = "requested_glosses_not_found"


class JmdictGlossResolutionError(RuntimeError):
    """A canonical identity cannot be projected through the configured JMdict packs."""


@dataclass(frozen=True, slots=True)
class JmdictGlossSourceReference:
    dataset: str
    language: str
    version: str
    digest_sha256: str

    @property
    def compact_ref(self) -> str:
        return f"jmdict:{self.language}:{self.version}:{self.digest_sha256[:16]}"


@dataclass(frozen=True, slots=True)
class JmdictGlossLookup:
    identity: LexicalFormIdentity
    status: JmdictGlossLookupStatus
    meanings: tuple[str, ...]
    source: JmdictGlossSourceReference

    def __post_init__(self) -> None:
        if self.status is JmdictGlossLookupStatus.FOUND and not self.meanings:
            raise ValueError("found JMdict gloss lookup must contain meanings")
        if self.status is not JmdictGlossLookupStatus.FOUND and self.meanings:
            raise ValueError("missing JMdict gloss lookup must not contain meanings")


class JmdictGlossPack(Protocol):
    @property
    def language(self) -> str: ...

    @property
    def source(self) -> JmdictGlossSourceReference: ...

    def lookup_identity(self, identity: LexicalFormIdentity) -> JmdictGlossLookup: ...


class JmdictGlossPackProvider(Protocol):
    """Language-addressable provider; implementations may load supported packs lazily."""

    def is_supported_language(self, language: str) -> bool: ...

    def get_pack(self, language: str) -> JmdictGlossPack: ...


@dataclass(frozen=True, slots=True)
class JsonJmdictGlossPack:
    """Exact-identity gloss view over one already-loaded normalized JMdict pack."""

    language: str
    dictionary: JsonJmdictDictionary
    source: JmdictGlossSourceReference

    def __post_init__(self) -> None:
        if self.source.dataset != _DATASET:
            raise ValueError("JMdict gloss source dataset must be JMdict")
        if self.source.language != self.language:
            raise ValueError("JMdict gloss source language does not match its pack")
        if self.source.version != self.dictionary.version:
            raise ValueError("JMdict gloss source version does not match loaded dictionary")
        if self.source.digest_sha256 != self.dictionary.digest.hex():
            raise ValueError("JMdict gloss source digest does not match loaded dictionary")

    @classmethod
    def from_reviewed_pack(
        cls,
        pack: ResolvedJmdictPack,
        dictionary: JsonJmdictDictionary,
    ) -> JsonJmdictGlossPack:
        """Bind a loaded dictionary to reviewed language/provenance metadata."""
        manifest = pack.manifest
        if dictionary.version != manifest.source.source_version:
            raise JmdictGlossResolutionError(
                f"loaded {pack.product_language} JMdict version does not match reviewed pack"
            )
        if dictionary.digest.hex() != manifest.normalized.sha256:
            raise JmdictGlossResolutionError(
                f"loaded {pack.product_language} JMdict digest does not match reviewed pack"
            )
        if dictionary.entry_count != manifest.normalized.entry_count:
            raise JmdictGlossResolutionError(
                f"loaded {pack.product_language} JMdict entry count does not match reviewed pack"
            )
        return cls(
            language=pack.product_language,
            dictionary=dictionary,
            source=JmdictGlossSourceReference(
                dataset=_DATASET,
                language=pack.product_language,
                version=manifest.source.source_version,
                digest_sha256=manifest.normalized.sha256,
            ),
        )

    def lookup_identity(self, identity: LexicalFormIdentity) -> JmdictGlossLookup:
        entry = self.dictionary.lookup_identity(identity)
        if entry is not None:
            status = (
                JmdictGlossLookupStatus.FOUND
                if entry.meanings
                else JmdictGlossLookupStatus.GLOSSES_NOT_FOUND
            )
            return JmdictGlossLookup(
                identity=identity,
                status=status,
                meanings=entry.meanings,
                source=self.source,
            )
        status = (
            JmdictGlossLookupStatus.FORM_NOT_FOUND
            if self.dictionary.contains_entry(identity.entry_id)
            else JmdictGlossLookupStatus.ENTRY_NOT_FOUND
        )
        return JmdictGlossLookup(
            identity=identity,
            status=status,
            meanings=(),
            source=self.source,
        )


@dataclass(frozen=True, slots=True)
class LocalizedJmdictGloss:
    identity: LexicalFormIdentity
    requested_dictionary_language: str
    fallback_dictionary_language: str
    effective_dictionary_language: str
    meanings: tuple[str, ...]
    fallback_used: bool
    fallback_reason: JmdictGlossFallbackReason | None
    source: JmdictGlossSourceReference


class LocalizedJmdictGlossResolver:
    """Resolve glosses after Japanese lexical identity has already been chosen."""

    def __init__(self, provider: JmdictGlossPackProvider) -> None:
        self._provider = provider

    def resolve(
        self,
        identity: LexicalFormIdentity,
        *,
        requested_dictionary_language: str,
    ) -> LocalizedJmdictGloss:
        if identity.dictionary_namespace != DICTIONARY_NAMESPACE:
            raise JmdictGlossResolutionError(
                "localized JMdict gloss resolution requires a JMdict lexical identity"
            )

        if requested_dictionary_language == FALLBACK_DICTIONARY_LANGUAGE:
            lookup = self._require_fallback_hit(identity)
            return self._resolved(
                identity,
                requested_dictionary_language=requested_dictionary_language,
                lookup=lookup,
                fallback_used=False,
                fallback_reason=None,
            )

        if not self._provider.is_supported_language(requested_dictionary_language):
            return self._fallback(
                identity,
                requested_dictionary_language=requested_dictionary_language,
                reason=JmdictGlossFallbackReason.UNSUPPORTED_REQUESTED_LANGUAGE,
            )

        requested_pack = self._get_pack(requested_dictionary_language)
        requested_lookup = requested_pack.lookup_identity(identity)
        self._validate_lookup(requested_lookup, identity, requested_pack)
        if requested_lookup.status is JmdictGlossLookupStatus.FOUND:
            return self._resolved(
                identity,
                requested_dictionary_language=requested_dictionary_language,
                lookup=requested_lookup,
                fallback_used=False,
                fallback_reason=None,
            )

        return self._fallback(
            identity,
            requested_dictionary_language=requested_dictionary_language,
            reason=_fallback_reason(requested_lookup.status),
        )

    def _fallback(
        self,
        identity: LexicalFormIdentity,
        *,
        requested_dictionary_language: str,
        reason: JmdictGlossFallbackReason,
    ) -> LocalizedJmdictGloss:
        lookup = self._require_fallback_hit(identity)
        return self._resolved(
            identity,
            requested_dictionary_language=requested_dictionary_language,
            lookup=lookup,
            fallback_used=True,
            fallback_reason=reason,
        )

    def _require_fallback_hit(self, identity: LexicalFormIdentity) -> JmdictGlossLookup:
        fallback_pack = self._get_pack(FALLBACK_DICTIONARY_LANGUAGE)
        lookup = fallback_pack.lookup_identity(identity)
        self._validate_lookup(lookup, identity, fallback_pack)
        if lookup.status is not JmdictGlossLookupStatus.FOUND:
            raise JmdictGlossResolutionError(
                "mandatory English JMdict fallback is missing the exact canonical identity/form: "
                f"{lookup.status}"
            )
        return lookup

    def _get_pack(self, language: str) -> JmdictGlossPack:
        if not self._provider.is_supported_language(language):
            raise JmdictGlossResolutionError(
                f"required deterministic JMdict pack is unavailable: {language}"
            )
        try:
            pack = self._provider.get_pack(language)
        except LookupError as exc:
            raise JmdictGlossResolutionError(
                f"required deterministic JMdict pack could not be loaded: {language}"
            ) from exc
        if pack.language != language or pack.source.language != language:
            raise JmdictGlossResolutionError(
                f"JMdict provider returned mismatched language pack for {language}"
            )
        return pack

    @staticmethod
    def _validate_lookup(
        lookup: JmdictGlossLookup,
        identity: LexicalFormIdentity,
        pack: JmdictGlossPack,
    ) -> None:
        if lookup.identity != identity:
            raise JmdictGlossResolutionError("JMdict pack changed the canonical lexical identity")
        if lookup.source != pack.source:
            raise JmdictGlossResolutionError("JMdict lookup provenance does not match its pack")

    @staticmethod
    def _resolved(
        identity: LexicalFormIdentity,
        *,
        requested_dictionary_language: str,
        lookup: JmdictGlossLookup,
        fallback_used: bool,
        fallback_reason: JmdictGlossFallbackReason | None,
    ) -> LocalizedJmdictGloss:
        return LocalizedJmdictGloss(
            identity=identity,
            requested_dictionary_language=requested_dictionary_language,
            fallback_dictionary_language=FALLBACK_DICTIONARY_LANGUAGE,
            effective_dictionary_language=lookup.source.language,
            meanings=lookup.meanings,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            source=lookup.source,
        )


def _fallback_reason(status: JmdictGlossLookupStatus) -> JmdictGlossFallbackReason:
    reasons = {
        JmdictGlossLookupStatus.ENTRY_NOT_FOUND: (
            JmdictGlossFallbackReason.REQUESTED_ENTRY_NOT_FOUND
        ),
        JmdictGlossLookupStatus.FORM_NOT_FOUND: (
            JmdictGlossFallbackReason.REQUESTED_FORM_NOT_FOUND
        ),
        JmdictGlossLookupStatus.GLOSSES_NOT_FOUND: (
            JmdictGlossFallbackReason.REQUESTED_GLOSSES_NOT_FOUND
        ),
    }
    try:
        return reasons[status]
    except KeyError as exc:
        raise JmdictGlossResolutionError(
            f"cannot fall back from successful JMdict gloss lookup: {status}"
        ) from exc
