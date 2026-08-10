# Language-axis contract

MangaSensei currently analyzes **Japanese manga only**. Content language, study language, dictionary language, and UI locale are separate product axes. Changing one does not silently rewrite another after an explicit user preference.

## Language model

| Concept | Current contract |
| --- | --- |
| Content / study-target language | `ja` only |
| Study / explanation language | `pt-BR` (default) or `en` |
| Dictionary / deterministic JMdict language | requested `en` (default), `de`, or `pt-BR` |
| UI locale | `en` (fresh-browser default) or `pt-BR` |

Valid combinations therefore include UI English + study Portuguese + dictionary German, UI Portuguese + study English + dictionary English, and UI English + study Portuguese + a `pt-BR` dictionary request whose deterministic effective meanings are English fallback.

The API and browser do not infer study or dictionary language from browser locale, `Accept-Language`, nationality, the other language axis, or ambient signals. Content remains Japanese.

## Browser preferences and reader

Study language and dictionary language are explicit, independently validated browser-local preferences. Missing, malformed, stale, or inaccessible study-language storage falls back to `pt-BR`; dictionary-language storage falls back to `en`. Storage failures do not break the application.

The reader groups study language, dictionary language, and furigana as study preferences while keeping navigation and page-presentation controls such as fit and zoom separate. UI locale remains independently selectable in the application chrome.

Browser storage is a convenience for future requests. Persisted completed page data remains authoritative for what is currently displayed. A language reprojection never relabels the previous completed result as if the requested replacement had already succeeded.

While either reader-initiated study-language or dictionary-language reprocessing is active, the other language mutation is disabled so the two one-active-job operations cannot race. Stopping client polling is not backend cancellation; the queued job may continue.

## Study language

`POST /api/v1/pages` accepts an optional multipart `studyLanguage` field with `pt-BR` or `en`. Omitting it preserves the `pt-BR` default. Study language controls contextual translation, explanation, and grammar output. It does not change OCR, reading order, Sudachi tokenization, Japanese lexical acquisition, canonical dictionary identity, UI locale, or deterministic dictionary language.

A study-language-only request uses the existing protected and idempotent reprocess endpoint:

```json
{
  "studyLanguage": "en"
}
```

The language-only path reuses completed language-independent work instead of rerunning OCR, reading order, Sudachi, or the persisted deterministic Japanese linguistic analysis merely to change the explanation language. Optional Gemini receives the explicit validated study language in its structured prompt; Gemini remains optional.

## Dictionary language

Dictionary language controls only the persisted localized JMdict projection over the already-resolved canonical `LexicalFormIdentity`. It does not change OCR, Sudachi/tokenization, lexical span acquisition, Japanese JMdict candidate selection, study language, UI locale, content language, or Gemini vocabulary identity.

The browser exposes `English`, `German`, and `Portuguese (Brazil)` as requested dictionary languages. Fresh or invalid browser state defaults to English to preserve the established deterministic local dictionary behavior.

A dictionary-language-only request uses the same protected and idempotent reprocess endpoint with exactly one language axis:

```json
{
  "dictionaryLanguage": "de"
}
```

Supported requested values are `en`, `de`, and `pt-BR`. The dictionary-only worker reuses the latest completed linguistic run and persisted lexical matches. It performs no OCR, Sudachi/lexical acquisition, or Gemini call.

The upload endpoint continues to create the normal initial English dictionary projection. After that completed result becomes readable, a browser whose stored dictionary preference differs from the persisted requested language starts dictionary-only reprojection. The completed result remains visible while this happens.

On successful reprojection, the reader fetches the newly completed page and persists the requested language reported by that result. On failure, the previous completed page stays visible and the browser selector/storage return to the requested dictionary language of that still-visible result.

## Requested versus effective dictionary language

The legacy page-level `dictionaryLanguage: "en"` remains for compatibility and is not the authoritative requested language for new clients. New page responses may expose `requestedDictionaryLanguage`, `fallbackDictionaryLanguage`, and `dictionarySources`; vocabulary items may expose `effectiveLanguage`, `fallbackUsed`, `fallbackReason`, and `sourceRef`.

The browser renders vocabulary meaning `lang` metadata from each item's **effective** language. Japanese lemma/reading remain `lang="ja"`. A legacy response without the newer projection fields is interpreted only as the historical English case: requested English, effective English, no fallback.

Fallback semantics are deterministic and exact-form preserving:

1. `en` requested: effective English, no fallback.
2. `de` requested with an exact German hit: effective German.
3. `de` requested when the exact canonical entry/form/glosses are unavailable: the same canonical identity/form falls back per item to English. German and English senses are never merged or aligned by ordinal.
4. `pt-BR` requested: the request remains `pt-BR`, but deterministic meanings are effective English with unsupported-request fallback provenance because there is no reviewed word-level Portuguese JMdict pack.

The reader identifies the requested dictionary language at panel level, marks English fallback items explicitly, and explains the `pt-BR` fallback without presenting English meanings as Portuguese. It uses `dictionarySources` and each item's `sourceRef` for compact JMdict/effective-language provenance rather than reproducing raw digests on every card.

See [Reviewed JMdict language packs](jmdict-packs.md) for pack integrity, exact-form resolution, fallback semantics, and runtime loading details.

## Persistence and migration

Existing historical results retain their established interpretation: content `ja`, study `pt-BR`, dictionary requested/effective English, and no fallback. New dictionary projections are immutable views over the persisted canonical linguistic run; they do not rewrite lexical identity.

When a newer language job is pending or fails, `resultAvailable` may remain true and protected page reads continue to expose the last completed result. Page retention remains the governing lifecycle for jobs, results, linguistic data, dictionary projections, and image storage.

## Privacy boundary

The original manga image, local JMdict data, and deterministic dictionary meanings are not sent to Gemini. Optional enrichment receives OCR text plus the minimum region-scoped lexical candidate fields required by the current contract. With Gemini disabled, Japanese OCR, deterministic linguistic analysis, and local JMdict projection continue to work; contextual translation/explanation may be absent.

## Assurance

Deterministic unit/component coverage verifies independent browser preferences, malformed/storage-failure fallback, exact dictionary reprocess payload/capability/idempotency behavior, retained previous results during pending/failure, requested-versus-effective language presentation, German direct and mixed English fallback, `pt-BR` requested with effective English, legacy English compatibility, and preserved multi-token lexical rendering.

Mocked Playwright covers desktop/mobile/accessibility behavior, keyboard-operable language controls, persistence across re-entry, German and fallback presentation, UI/study/dictionary independence, furigana, fit/zoom, and focus behavior.

The required real full-stack Playwright flow runs against FastAPI, PostgreSQL, real migrations, page capabilities, queue/fencing, and the real dictionary-projection worker. Heavy OCR and paid Gemini network boundaries are deterministically replaced. The flow proves browser `en` -> `de` dictionary reprojection through persisted localized JMdict data without downloading the production review packs.

## Current product boundary

This feature does **not** add multilingual manga OCR, content-language detection, Spanish dictionary packs, generated Portuguese dictionary translations, or Gemini translation of deterministic dictionary meanings. Content remains Japanese and the original image is never replaced with translated text.
