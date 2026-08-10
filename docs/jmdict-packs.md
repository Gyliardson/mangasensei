# Reviewed JMdict language packs

MangaSensei keeps deterministic dictionary data separate from Japanese content language,
study/explanation language, and UI locale.

The reviewed pack registry is
[`backend/src/mangasensei/linguistics/jmdict_packs.json`](../backend/src/mangasensei/linguistics/jmdict_packs.json).
Each registry entry maps a product dictionary language tag to an upstream
`jmdict-simplified` language code and to an independently integrity-pinned pack manifest.
All packs in one registry must use the same reviewed source snapshot.

Current reviewed packs:

- `en` -> upstream `eng`: default deterministic dictionary pack and mandatory future fallback;
- `de` -> upstream `ger`: additional deterministic pack foundation.

This infrastructure slice does **not** expose requested/effective dictionary language in the
reader or persisted study results. The worker therefore continues to load the configured
English `MANGASENSEI_JMDICT_PATH` exactly as before. German is downloaded or verified only
when selected explicitly through the artifact CLI:

```text
mangasensei jmdict download --language de
mangasensei jmdict verify --language de
```

Without `--language`, both commands retain the established English behavior. Non-English
normalized packs are placed beside the configured English path using the normalized filename
from their reviewed manifest; the current German filename is `jmdict-de.json`.

Unknown product languages fail closed. There is no reviewed word-level Portuguese JMdict pack,
and KANJIDIC Portuguese data is not used as a vocabulary substitute.

## Lexical matching boundary

The pack layer owns reviewed data acquisition, normalization and integrity only. It continues to
produce the established `mangasensei-jmdict-v3` form contract and does not call or define the
runtime lexical-candidate selection API. #103 owns language-neutral Japanese lexical matching;
future gloss-language resolution must consume the canonical lexical identity produced by that
layer rather than using dictionary language to influence candidate selection. This keeps the pack
foundation independent from #103 persistence/read-model changes and from the lookup API evolution
currently under review in PR #111.

## Refreshing reviewed metadata

The converter remains `mangasensei-jmdict-v3`, including spelling/reading/sense applicability
semantics. Recompute normalized metadata from the exact pinned sources with:

```text
uv run python scripts/update_jmdict_manifest.py
```

Use `--check` in verification/CI and `--language en` or `--language de` to limit the operation
to one reviewed product-language pack. The updater never chooses a newer upstream release; it
only consumes the source URLs, sizes, and SHA-256 values already present in the reviewed pack
manifests.

The dedicated JMdict contract workflow downloads and validates every reviewed pack, exercises
the default-English and explicit-German CLI paths, and records one-process loader measurements.
Those RSS/load-time observations are implementation evidence only, not product limits.
