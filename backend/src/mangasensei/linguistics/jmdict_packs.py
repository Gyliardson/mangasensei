"""Reviewed JMdict language-pack registry and explicit pack selection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mangasensei.linguistics.jmdict_bootstrap import (
    CONVERTER_VERSION,
    JmdictIntegrityError,
    JmdictManifest,
    download_jmdict,
    verify_jmdict,
)

DEFAULT_DICTIONARY_LANGUAGE = "en"
FALLBACK_DICTIONARY_LANGUAGE = "en"


class JmdictPackDescriptor(BaseModel):
    """Language mapping for one independently pinned JMdict pack manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    product_language: str = Field(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    upstream_language: str = Field(pattern=r"^[a-z]{3}$")
    manifest: str = Field(pattern=r"^[A-Za-z0-9._-]+\.json$")


class JmdictPackRegistry(BaseModel):
    """Reviewed set of interoperable JMdict packs from one source snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: str = Field(min_length=1, max_length=64)
    source_snapshot: str = Field(min_length=1, max_length=57)
    default_language: str = Field(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    fallback_language: str = Field(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")
    packs: tuple[JmdictPackDescriptor, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_languages(self) -> JmdictPackRegistry:
        languages = [pack.product_language for pack in self.packs]
        if len(languages) != len(set(languages)):
            raise ValueError("JMdict pack registry contains duplicate product languages")
        if self.default_language != DEFAULT_DICTIONARY_LANGUAGE:
            raise ValueError("JMdict default dictionary language must remain English")
        if self.fallback_language != FALLBACK_DICTIONARY_LANGUAGE:
            raise ValueError("JMdict deterministic fallback language must remain English")
        if self.default_language not in languages or self.fallback_language not in languages:
            raise ValueError("JMdict registry must contain its default and fallback packs")
        upstream_languages = [pack.upstream_language for pack in self.packs]
        if len(upstream_languages) != len(set(upstream_languages)):
            raise ValueError("JMdict pack registry contains duplicate upstream languages")
        manifests = [pack.manifest for pack in self.packs]
        if len(manifests) != len(set(manifests)):
            raise ValueError("JMdict pack registry contains duplicate manifest files")
        return self

    @classmethod
    def load(cls, path: Path) -> JmdictPackRegistry:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class ResolvedJmdictPack:
    product_language: str
    upstream_language: str
    manifest_path: Path
    manifest: JmdictManifest
    default_language: str

    def target_path(self, configured_english_path: Path) -> Path:
        """Keep the established English path while placing other packs beside it."""
        if self.product_language == self.default_language:
            return configured_english_path
        return configured_english_path.parent / self.manifest.normalized.filename


def default_pack_registry_path() -> Path:
    return Path(__file__).with_name("jmdict_packs.json")


def load_jmdict_packs(
    registry_path: Path | None = None,
) -> dict[str, ResolvedJmdictPack]:
    """Load and cross-check every reviewed pack before any one is selected."""
    path = registry_path or default_pack_registry_path()
    registry = JmdictPackRegistry.load(path)
    resolved: dict[str, ResolvedJmdictPack] = {}
    normalized_filenames: set[str] = set()
    for descriptor in registry.packs:
        manifest_path = path.with_name(descriptor.manifest)
        manifest = JmdictManifest.load(manifest_path)
        if manifest.source.language != descriptor.upstream_language:
            raise JmdictIntegrityError(
                f"dictionary upstream language mismatch: {descriptor.product_language}"
            )
        if manifest.source.source_version != registry.source_snapshot:
            raise JmdictIntegrityError(
                f"dictionary source snapshot mismatch: {descriptor.product_language}"
            )
        if manifest.normalized.converter_version != CONVERTER_VERSION:
            raise JmdictIntegrityError(
                f"dictionary converter mismatch: expected {CONVERTER_VERSION}"
            )
        if manifest.normalized.filename in normalized_filenames:
            raise JmdictIntegrityError("dictionary packs must use distinct normalized filenames")
        normalized_filenames.add(manifest.normalized.filename)
        resolved[descriptor.product_language] = ResolvedJmdictPack(
            product_language=descriptor.product_language,
            upstream_language=descriptor.upstream_language,
            manifest_path=manifest_path,
            manifest=manifest,
            default_language=registry.default_language,
        )
    return resolved


def resolve_jmdict_pack(
    language: str = DEFAULT_DICTIONARY_LANGUAGE,
    *,
    registry_path: Path | None = None,
) -> ResolvedJmdictPack:
    packs = load_jmdict_packs(registry_path)
    try:
        return packs[language]
    except KeyError as exc:
        raise JmdictIntegrityError(f"unsupported dictionary language: {language}") from exc


async def download_jmdict_pack(
    configured_english_path: Path,
    *,
    language: str = DEFAULT_DICTIONARY_LANGUAGE,
    registry_path: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    pack = resolve_jmdict_pack(language, registry_path=registry_path)
    return await download_jmdict(
        pack.target_path(configured_english_path),
        manifest_path=pack.manifest_path,
        client=client,
    )


def verify_jmdict_pack(
    configured_english_path: Path,
    *,
    language: str = DEFAULT_DICTIONARY_LANGUAGE,
    registry_path: Path | None = None,
) -> Path:
    pack = resolve_jmdict_pack(language, registry_path=registry_path)
    return verify_jmdict(
        pack.target_path(configured_english_path),
        manifest_path=pack.manifest_path,
    )
