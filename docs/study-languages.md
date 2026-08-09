# Study-language contract

MangaSensei currently analyzes **Japanese manga only**. Study language controls learner-facing language-dependent content; it does not change the manga content language, OCR engine, reading order, Sudachi tokenization, or deterministic Japanese analysis.

## Language model

Three concepts are intentionally separate:

| Concept | Current contract |
| --- | --- |
| Content / study-target language | `ja` only |
| Study / explanation language | `pt-BR` (default) or `en` |
| UI locale | Independent from study language; the current application UI remains Portuguese while UI localization evolves separately |

The API does not infer study language from browser locale, `Accept-Language`, UI copy, nationality, or any other ambient signal.

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

When a newer reprocessing attempt is pending or fails, `resultAvailable` may remain true and the API continues to expose the previous completed result together with that result's effective language metadata.

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

MangaSensei does not introduce or synthesize a Portuguese deterministic dictionary source solely to make the study-language selector appear uniform.

## Persistence and migration

Existing completed analyses created before the explicit study-language schema are migrated as:

- content language: `ja`;
- study language: `pt-BR`;
- dictionary language: `en`.

This preserves the pre-existing Portuguese study-flow interpretation while retaining the English JMdict provenance.

Study-language-only results can reference an earlier linguistic run. Page retention remains the governing lifecycle: deleting an expired page still cascades through the related jobs/results and their reused analysis graph according to the existing retention model.

## Current product boundary

This contract does **not** add English, Portuguese, Spanish, or generic multilingual manga OCR. It also does not automatically localize the application UI. The current feature boundary is Japanese content studied with either Brazilian Portuguese or English learner-facing contextual content.

The user-facing study-language selector and browser-local preference are delivered separately from this backend contract so they can integrate with the reader's current control hierarchy without coupling UI locale to study language.
