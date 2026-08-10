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

There is no reviewed word-level Portuguese JMdict pack. `pt-BR` can be requested through the
backend dictionary-projection contract, but its deterministic effective vocabulary is English with
`unsupported_requested_language`; it is never represented as Portuguese JMdict. KANJIDIC
Portuguese data and generated translations are not used as vocabulary substitutes.

Artifact bootstrap remains explicit:

```text
mangasensei jmdict download --language de
mangasensei jmdict verify --language de
```

Without `--language`, both commands retain the established English behavior. Non-English
normalized packs are placed beside the configured English path using the normalized filename from
their reviewed manifest; the current German filename is `jmdict-de.json`.

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
4. `pt-BR` requested: use the same exact English identity/form with
   `unsupported_requested_language` fallback provenance. This does not represent a Portuguese
   JMdict pack or generated Portuguese vocabulary.

If the mandatory English pack cannot resolve the exact identity/form, resolution fails closed.

## Durable result projection

Dictionary language is persisted as an immutable projection over an already-completed canonical
linguistic run. Historical `StudyResult.dictionary_language` remains the backward-compatible
English-only field; it is not redefined to mean the requested multilingual policy.

Each completed projection stores:

- result-level `requested_dictionary_language` and mandatory `fallback_dictionary_language = en`;
- normalized source records with dataset, effective product language, reviewed source version and
  normalized SHA-256 digest;
- one projection item per canonical lexical match with effective language, fallback flag/reason and
  source reference;
- ordered localized meanings separately from source metadata.

Existing English StudyResults are backfilled to equivalent `requested=en`, `fallback=en`,
`effective=en`, `fallback_used=false` projections using the persisted linguistic-run JMdict version
and digest plus the historical ordered English meanings. The existing language-neutral lexical
matches and identities are not rewritten.

A multilingual downgrade is intentionally blocked because removing these tables would discard
requested/effective/fallback state that the previous schema cannot represent losslessly.

## Dictionary-only reprojection API

The existing capability-protected and idempotent page reprocess endpoint accepts one language axis
at a time. For dictionary reprojection, clients send a body equivalent to:

```json
{"dictionaryLanguage":"de"}
```

Supported requested values are `en`, `de`, and `pt-BR`. The page capability and
`Idempotency-Key` requirements are unchanged. Dictionary and study/explanation language are
independent; neither is inferred from UI locale or from the other language axis.

A dictionary-only job reuses the latest completed StudyResult's exact `LinguisticRun` and
`LexicalMatch` rows. It performs no OCR, Sudachi/tokenization, lexical acquisition, or Gemini call.
The previously completed page remains readable while a new projection is pending or if the new job
fails. Normal page/job ownership and 24-hour retention continue to govern projection rows through
foreign-key cascades.

Protected page JSON keeps the legacy `dictionaryLanguage: "en"` field and adds:

- `requestedDictionaryLanguage`;
- `fallbackDictionaryLanguage`;
- `dictionarySources[]` with `ref`, `dataset`, `productLanguage`, `sourceVersion`, and
  `normalizedDigestSha256`;
- per-vocabulary `effectiveLanguage`, `fallbackUsed`, `fallbackReason`, `sourceRef`, and the
  authoritative projected `meanings`.

An English response remains a natural compatible subset: requested/effective/fallback are English,
`fallbackUsed` is false, and existing vocabulary fields retain their previous meaning.

No browser dictionary selector or preference is implemented by this backend slice.

## Runtime loading and memory

The production worker reuses its already-loaded mandatory English `JsonJmdictDictionary` for both
Japanese candidate acquisition and English gloss projection. The language-addressable provider
loads German only on the first German request in that worker process, after resolving and verifying
the reviewed pinned pack. It keeps at most that one optional German pack; there is no unbounded or
global cache and no runtime download of unpinned artifacts.

The reviewed loader measurement gate previously observed substantial one-process residency (about
862 MiB max RSS for English and about 545 MiB for German in the measured environment). Those
measurements are implementation evidence, not product limits, and are why German is not eagerly
loaded in every worker.

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
default-English and explicit-German CLI paths, runs focused resolver/lazy-provider/producer/pack
regressions, and records one-process loader measurements.
