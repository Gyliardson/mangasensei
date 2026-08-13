# Language-axis contract

MangaSensei currently analyzes **Japanese manga only**. Content language, study/explanation language,
deterministic dictionary language, and UI locale are distinct concepts.

## Current language model

| Concept | Current contract |
| --- | --- |
| Content / study-target language | `ja` only |
| Study / explanation language | `pt-BR` (default) or `en` |
| Dictionary / deterministic JMdict language | `en` only |
| UI locale | `en` (fresh-browser default) or `pt-BR` |

The English-only dictionary decision does not reduce the study or UI language choices. For example,
UI English + study Portuguese still uses English deterministic JMdict meanings while contextual
translation/explanation remains Portuguese.

The API and browser do not infer study language from browser locale, `Accept-Language`, nationality,
or ambient signals. Content remains Japanese.

## Browser preferences and reader

Study language remains an explicit, validated browser-local preference. Missing, malformed, stale, or
inaccessible study-language storage falls back to `pt-BR`. UI locale remains independently
selectable in the application chrome.

The reader no longer exposes or persists an independent dictionary-language preference. On upgrade,
any legacy browser value under the retired dictionary-language key is best-effort deleted, including
old `en`, `de`, `pt-BR`, or malformed values. Current deterministic dictionary behavior is simply
English and migration never creates a dictionary reprojection request.

Browser storage is a convenience for future study/UI requests. Persisted completed page data remains
authoritative for what is currently displayed. Stopping client polling is not backend cancellation;
a queued job may continue.

## Study language

`POST /api/v1/pages` accepts an optional multipart `studyLanguage` field with `pt-BR` or `en`.
Omitting it preserves the `pt-BR` default. Study language controls contextual translation,
explanation, and grammar output. It does not change OCR, reading order, Sudachi tokenization,
Japanese lexical acquisition, canonical dictionary identity, UI locale, or English deterministic
JMdict meanings.

A study-language-only request uses the existing protected and idempotent reprocess endpoint:

```json
{
  "studyLanguage": "en"
}
```

The language-only path reuses completed language-independent work instead of rerunning OCR, reading
order, Sudachi, or the persisted deterministic Japanese linguistic analysis merely to change the
explanation language. Optional Gemini receives the explicit validated study language in its
structured prompt; Gemini remains optional.

## Dictionary language

The active deterministic dictionary contract is English-only. Normal current results therefore use
English for requested, fallback, and effective dictionary language.

Canonical Japanese lexical acquisition is unchanged: MangaSensei first resolves an exact
`LexicalFormIdentity`, then uses the reviewed English JMdict pack for meanings. OCR, Sudachi,
lexical-span selection, study language, UI locale and Gemini vocabulary identity are unaffected.

Applied dictionary-projection migrations and their response fields are intentionally retained for
safe upgrades. An unexpired result created by an older build may still contain historical
`requestedDictionaryLanguage`, `fallbackDictionaryLanguage`, `dictionarySources`,
`effectiveLanguage`, `fallbackUsed`, `fallbackReason`, and `sourceRef` metadata. Current code does not
rewrite those historical rows merely because the active product stopped offering additional packs.

New application behavior does not offer non-English dictionary reprojection. The legacy persistence
machinery remains only so existing database state and in-flight/historical records can be consumed
safely during rolling upgrades.

See [Reviewed JMdict data](jmdict-packs.md) for the English pack's integrity and bootstrap contract.

## Persistence and migration

Historical projection tables are kept because editing or deleting an applied migration would make
existing installations unsafe to upgrade. This is schema compatibility, not an indication that
German or Portuguese dictionary packs remain supported.

When a newer study-language job is pending or fails, `resultAvailable` may remain true and protected
page reads continue to expose the last completed result. Page retention remains the governing
lifecycle for jobs, results, linguistic data, historical dictionary projections, and image storage.

## Privacy boundary

The original manga image, local JMdict data, and deterministic dictionary meanings are not sent to
Gemini. Optional enrichment receives OCR text plus the minimum region-scoped lexical candidate
fields required by the current contract. With Gemini disabled, Japanese OCR, deterministic
linguistic analysis, and local English JMdict meanings continue to work; contextual
translation/explanation may be absent.

## Assurance

Tests must preserve the separation between:

- Japanese content language;
- `pt-BR` / `en` study language;
- English deterministic dictionary meanings;
- `pt-BR` / `en` UI locale.

The dedicated JMdict contract validates the pinned English source, normalized metadata, runtime
provider, clean production Compose bootstrap, and one local-only analysis. Frontend tests verify that
the retired dictionary-language key is removed rather than reintroduced as a preference while
study-language and UI-locale controls remain functional.
