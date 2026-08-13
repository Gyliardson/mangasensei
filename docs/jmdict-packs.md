# Reviewed JMdict data

MangaSensei uses one deterministic local vocabulary dataset: the reviewed English JMdict pack.
Dictionary data remains separate from Japanese content language, study/explanation language, and UI
locale.

The reviewed registry is
[`backend/src/mangasensei/linguistics/jmdict_packs.json`](../backend/src/mangasensei/linguistics/jmdict_packs.json).
It contains only:

- product language `en` -> upstream `eng`;
- source snapshot `jmdict-simplified-3.6.2+20260803141815`;
- normalized artifact `jmdict.json`;
- converter `mangasensei-jmdict-v3`.

The exact source URL, byte size, SHA-256, normalized SHA-256, entry count, attribution and license are
pinned in
[`jmdict_manifest.json`](../backend/src/mangasensei/linguistics/jmdict_manifest.json).

## Product language contract

The local dictionary is English-only.

This does **not** change either of the other language axes:

- study/explanation language remains independently selectable (`pt-BR` or `en`);
- UI locale remains independently selectable.

The reader no longer exposes a dictionary-language selector. New browser preference state is always
English, and retired `de` / `pt-BR` dictionary-preference values are normalized back to English.

Applied dictionary-projection migrations are intentionally retained. They may contain historical
requested/effective/fallback metadata created by older builds; removing or rewriting applied schema
would make upgrades unsafe. Historical response metadata may therefore still describe an older
requested language, while the current runtime has only the English pack available. The canonical
Japanese lexical identity and stored historical rows are not rewritten by this simplification.

## Bootstrap and verification

Provision the reviewed local dictionary with:

```text
mangasensei jmdict download
mangasensei jmdict verify
```

Production Compose runs only that English bootstrap before workers start. There is no German pack
download, normalized German artifact, optional-pack runtime load, or second dictionary network
dependency.

Every downloaded source remains fail-closed:

- HTTPS is required;
- source byte size must match the manifest;
- source SHA-256 must match the manifest;
- ZIP structure and uncompressed-size limits are enforced;
- normalized bytes, entry count and source version must match the reviewed manifest.

## Lexical matching boundary

The English-only decision does not change Japanese lexical acquisition. `LinguisticService` selects
one canonical `LexicalFormIdentity` before meanings are projected. `JsonJmdictDictionary` resolves
that exact entry/form identity; spelling, reading and sense restrictions from the
`mangasensei-jmdict-v3` converter remain intact.

The existing projection persistence is retained for upgrade compatibility, but ordinary current
results use English as requested, fallback and effective deterministic dictionary language.

## Runtime loading and memory

The production worker reuses the mandatory English `JsonJmdictDictionary` that is already loaded for
canonical lexical acquisition. No optional language pack is lazily loaded. This removes the former
German pack's additional bootstrap, disk and process-residency cost.

The loader measurement gate therefore records only the English pack. Measurements are implementation
evidence, not product memory limits.

## Refreshing reviewed metadata

Recompute/check metadata from the exact already-pinned English source with:

```text
uv run python scripts/update_jmdict_manifest.py
uv run python scripts/update_jmdict_manifest.py --check
```

The updater does not select a newer upstream release. Updating to another JMdict snapshot remains a
separate reviewed dependency/data change.

The dedicated JMdict Data Contract workflow validates the English source and normalized artifact,
focused bootstrap/registry/runtime tests, the English CLI path, a clean production Compose bootstrap,
and one local-only analysis through the resulting stack.
