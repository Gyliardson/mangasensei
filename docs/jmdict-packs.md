# Reviewed JMdict language packs

MangaSensei keeps deterministic dictionary data separate from Japanese content language,
study/explanation language, and UI locale.

The reviewed pack registry is
[`backend/src/mangasensei/linguistics/jmdict_packs.json`](../backend/src/mangasensei/linguistics/jmdict_packs.json).
Each registry entry maps a product dictionary language tag to an upstream
`jmdict-simplified` language code and to an independently integrity-pinned pack manifest.
All packs in one registry must use the same reviewed source snapshot.

Current reviewed packs:

- `en` -> upstream `eng`: default deterministic dictionary pack and mandatory fallback;
- `de` -> upstream `ger`: additional deterministic pack.

This implementation does **not** expose requested/effective dictionary language in the reader or
persisted study results. The worker therefore continues to load the configured English
`MANGASENSEI_JMDICT_PATH` exactly as before. German is downloaded or verified only when selected
explicitly through the artifact CLI:

```text
mangasensei jmdict download --language de
mangasensei jmdict verify --language de
```

Without `--language`, both commands retain the established English behavior. Non-English
normalized packs are placed beside the configured English path using the normalized filename from
their reviewed manifest; the current German filename is `jmdict-de.json`.

Unknown product languages still fail closed at the artifact CLI/pack-selection boundary. There is
no reviewed word-level Portuguese JMdict pack, and KANJIDIC Portuguese data is not used as a
vocabulary substitute.

## Lexical matching boundary

The pack layer owns reviewed data acquisition, normalization and integrity. It continues to produce
the established `mangasensei-jmdict-v3` form contract. Canonical Japanese lexical acquisition is
language-neutral: `LinguisticService` and the SplitMode.A span acquisition added by #111/#112 choose
one unambiguous `LexicalFormIdentity` before dictionary-language projection begins.

[`jmdict_glosses.py`](../backend/src/mangasensei/linguistics/jmdict_glosses.py) consumes only that
already-resolved identity. `JsonJmdictDictionary.lookup_identity()` performs an exact
namespace/entry/form lookup and does not rerun Japanese candidate selection in the target-language
pack. Gloss text and dictionary language are therefore not part of `LexicalFormIdentity`.

## Localized gloss resolution and fallback

The localized resolver is all-or-nothing per exact canonical identity/form:

1. English requested: return the exact English form with no fallback.
2. German requested and the exact German entry/form has glosses: return the complete German meaning
   tuple in its deterministic pack order. English senses are not aligned or merged by ordinal.
3. German requested but the entry, exact canonical form, or gloss set is unavailable: resolve the
   same exact identity/form in English and report an explicit machine-readable fallback reason.
4. An unsupported requested language such as `pt-BR`: use the same exact English identity/form with
   `unsupported_requested_language` fallback provenance. This does not represent a Portuguese
   JMdict pack or generated Portuguese vocabulary.

A result carries the unchanged lexical identity, requested/fallback/effective dictionary language,
meanings, fallback state/reason, and effective JMdict language/version/SHA-256 source reference.
If the mandatory English pack cannot resolve the exact identity/form, resolution fails closed.

The resolver receives a language-addressable provider through dependency injection. It does not
construct a global cache or eagerly instantiate every reviewed JSON pack. Provider implementations
may load supported packs lazily; this slice deliberately does not wire German pack loading into
workers because the current JSON loader has substantial per-process memory cost and the
persistence/API language-selection slice is still pending.

## Refreshing reviewed metadata

The converter remains `mangasensei-jmdict-v3`, including spelling/reading/sense applicability
semantics. Recompute normalized metadata from the exact pinned sources with:

```text
uv run python scripts/update_jmdict_manifest.py
```

Use `--check` in verification/CI and `--language en` or `--language de` to limit the operation to
one reviewed product-language pack. The updater never chooses a newer upstream release; it only
consumes the source URLs, sizes, and SHA-256 values already present in the reviewed pack manifests.

The dedicated JMdict contract workflow downloads and validates every reviewed pack, exercises the
default-English and explicit-German CLI paths, runs focused resolver/producer/pack regressions, and
records one-process loader measurements. Those RSS/load-time observations are implementation
evidence only, not product limits.
