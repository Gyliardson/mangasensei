# Study-language contract

MangaSensei currently analyzes **Japanese manga only**. Study language controls learner-facing language-dependent content; it does not change the manga content language, OCR engine, reading order, Sudachi tokenization, or deterministic Japanese analysis.

## Language model

Three concepts are intentionally separate:

| Concept | Current contract |
| --- | --- |
| Content / study-target language | `ja` only |
| Study / explanation language | `pt-BR` (default) or `en` |
| UI locale | Independent from study language; the current application UI remains Portuguese (`pt-BR`) |

The API does not infer study language from browser locale, `Accept-Language`, UI copy, nationality, or any other ambient signal. The browser exposes an explicit study-language preference instead.

## Browser preference and reader

The upload screen and reader expose `Português (Brasil)` and `Inglês` as study-language choices. The preference is stored locally in the browser under a validated two-value contract. Missing, malformed, or inaccessible browser storage falls back to `pt-BR` without breaking the application.

Browser storage is a convenience for the next request; it is not the source of truth for already generated content. The reader displays the effective `studyLanguage` returned by the persisted completed result. If a language-only reprocessing request fails, the previous completed result remains visible and the browser selector is restored to that effective language rather than pretending the failed preference was generated successfully.

Study language is part of the reader's **study preferences** alongside furigana. It is intentionally separate from navigation and from page-presentation controls such as fit and zoom.

The document/UI locale remains `pt-BR` when English study content is selected. Contextual English output is marked with `lang="en"`, Japanese text with `lang="ja"`, and deterministic local JMdict meanings with `lang="en"`.

## Upload API

`POST /api/v1/pages` accepts an optional multipart form field named `studyLanguage` in addition to the image. Supported values are `pt-BR` and `en`. Omitting the field preserves backward compatibility and selects `pt-BR`.

The upload response includes the effective `studyLanguage` associated with the queued job. Unsupported or malformed values are rejected by the normal request-validation contract; they are not silently mapped to another language.

Replaying an upload idempotency key with a different study language is treated as an idempotency conflict rather than reinterpreting the original request.

## Page result metadata

`GET /api/v1/pages/{page_id}` identifies the language contract of the **persisted completed result** with:

- `contentLanguage`: currently always `ja`;
- `studyLanguage`: `pt-BR` or `en` for the completed learner-facing result;
- `dictionaryLanguage`: currently `en`, reflecting the reviewed local JMdict dataset used by MangaSensei.

This metadata belongs to the persisted result. A browser preference change cannot silently reinterpret an existing English result as Portuguese or vice versa.

When a newer reprocessing attempt is pending or fails, `resultAvailable` may remain true and the API continues to expose the previous completed result together with that result's effective language metadata. The browser keeps rendering that completed result while a requested language replacement is still being generated.

## Reprocessing a study language

`POST /api/v1/pages/{page_id}/reprocess` keeps its existing page capability and idempotency requirements. An optional JSON body can request a study language:

```json
{
  "studyLanguage": "en"
}
```

When the page already has a completed analysis and only the study language is explicitly changed, MangaSensei creates a language-reprocessing job that reuses the completed language-independent work. It does not re-upload or decode the image and does not rerun OCR, reading order, Sudachi, or the persisted deterministic Japanese linguistic analysis merely to change the explanation language.

If no study language is supplied, reprocessing preserves the latest completed result's study language when available; pre-language/default flows remain `pt-BR`.

The endpoint keeps the same authorization boundary: language is request/result metadata, never an authorization input. Reusing one idempotency key for a different reprocessing mode or study language returns an idempotency conflict.

## Gemini boundary

Gemini remains optional. When configured, the validated study language is included explicitly in the structured page-study prompt contract. The request digest covers that exact prompt, so changing study language also changes the provider request digest truthfully.

The privacy boundary is unchanged: the original manga image and local JMdict dataset/meanings are not sent to Gemini. Optional enrichment receives OCR text plus the minimum region-scoped lexical candidate fields required by the current contract.

With Gemini disabled, Japanese OCR and linguistic analysis continue locally. Contextual translation/explanation fields may be absent because MangaSensei does not fabricate deterministic multilingual contextual content.

## Deterministic local vocabulary

The currently reviewed/pinned JMdict pipeline is English-backed. Its local meanings therefore remain deterministic English dictionary meanings even when the selected study language is `pt-BR`.

Dictionary meaning and contextual translation/explanation are separate concepts:

- JMdict supplies deterministic local lexical meanings and stable dictionary identifiers;
- optional Gemini supplies language-dependent contextual translation, explanation, and grammar presentation.

MangaSensei does not introduce or synthesize a Portuguese deterministic dictionary source solely to make the study-language contract appear uniform.

## Persistence and migration

Existing completed analyses created before the explicit study-language schema are migrated as:

- content language: `ja`;
- study language: `pt-BR`;
- dictionary language: `en`.

This preserves the pre-existing Portuguese study-flow interpretation while retaining the English JMdict provenance.

Study-language-only results can reference an earlier linguistic run. Page retention remains the governing lifecycle: deleting an expired page still cascades through the related jobs/results and their reused analysis graph according to the existing retention model.

## Assurance

The deterministic unit/integration layers verify:

- `pt-BR` is the default and unsupported study-language values are rejected;
- browser preference persistence has deterministic fallback for malformed or inaccessible storage;
- the Gemini prompt contains the explicit validated study language and its persisted request digest remains tied to the exact prompt;
- a real pre-language database state with a completed analysis upgrades to `pt-BR` result metadata without losing the existing linguistic run;
- `pt-BR` → `en` language reprocessing creates a new study result while retaining exactly one OCR run and one linguistic run;
- the previously completed result and its language remain readable while the new language job is pending;
- English study metadata remains valid with Gemini disabled and deterministic local JMdict vocabulary still works;
- normal analysis jobs cannot use the direct state transitions reserved for language-only reuse jobs;
- mocked browser coverage exercises desktop/mobile/accessibility behavior and verifies that UI locale, study language, Japanese content and English dictionary meanings remain distinct.

The required real full-stack Playwright flow performs an actual browser upload against FastAPI, PostgreSQL and the queue/worker, receives a persisted `pt-BR` contextual result, requests `en` through the real reprocess endpoint and capability, waits for the worker, then reads the newly persisted English result back through the application. MangaSensei API requests are not intercepted. Heavy OCR inference and the paid Gemini network call are replaced only at their external boundaries with deterministic fixtures; API, migrations, capabilities, queue transitions, Sudachi/JMdict processing, persistence, reprocessing and browser behavior remain real.

## Current product boundary

This feature does **not** add English, Portuguese, Spanish, or generic multilingual manga OCR. Content remains Japanese and automatic content-language detection is not performed. It also does not translate or replace text inside the manga image.

The current browser UI remains Portuguese while study/explanation language can independently be Portuguese (Brazil) or English. Deterministic local JMdict meanings remain English regardless of the selected study language, and optional contextual translation/explanation remains absent when Gemini is disabled.
