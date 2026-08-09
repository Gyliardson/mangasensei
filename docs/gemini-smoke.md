# Gemini real-provider smoke

MangaSensei keeps ordinary development and CI credential-free. Gemini is optional, and the normal deterministic test suite must pass without a Google API key. The real-provider smoke is a separate, explicitly opt-in assurance layer for the current `GoogleGenAiAdapter` Interactions request.

See the broader [testing strategy](testing.md) for the deterministic CI, full-stack and OCR assurance boundaries.

## Local smoke

The real-provider test is marked `gemini_smoke` and starts with the actual `GoogleGenAiAdapter`, a synthetic one-region page-study prompt and the production `GeminiPageAnalysis` structured-output contract. It sends no manga image, user OCR text, local JMdict data or other user content. The production-shaped call retains the configured model, `store=False`, low thinking and the normal 16,384 output-token limit.

Run it with:

```sh
uv run pytest tests/test_gemini_smoke.py -m gemini_smoke -q
```

The test reads `GOOGLE_API_KEY` only from the process environment. If the variable is absent or empty, pytest reports the test as skipped with a reason stating that the real provider smoke was not executed. A skipped test is **not** evidence that the provider contract works.

Never commit `.env`, an API key, copied provider headers, or a secret-bearing request dump. Keep credentials in local secret storage or another private environment mechanism.

## Structured-output compatibility boundary

MangaSensei keeps the complete Pydantic response contract as the local validation authority. The Interactions request receives a provider-facing JSON Schema derived from that contract, while validation after generation still uses the original Pydantic model.

String `minLength`/`maxLength` constraints are omitted from the provider representation because they are outside the currently documented Gemini structured-output subset. `maxItems` is also omitted from the provider representation as a narrower compatibility workaround established by the real-provider #90 differential: the current production-shaped request was rejected, flat and nested `$defs`/`$ref` controls were accepted, and the first production-derived schema variant accepted by Interactions was the variant without `maxItems`. Google currently documents `maxItems` as supported, so MangaSensei does not treat it as universally unsupported; the omission is retained only at the provider boundary while the local Pydantic contract continues to enforce every array-length limit.

The real-provider smoke deliberately starts with the production page-analysis schema so an SDK/model/provider change that rejects this compatibility representation is visible before the optional Gemini path is considered validated.

While issue #90 remains under provider-contract investigation, a provider rejection of that first production-shaped request activates a bounded synthetic differential. The differential compares safe schema variants using a 128-token output cap and reports only labels plus sanitized provider status/category. It never prints the prompt, schema payload, provider response body, request headers or secret. The diagnostic stops at the first production-derived schema variant accepted by the provider and makes at most eight provider calls including the initial production-shaped request.

The differential is diagnostic evidence only. A diagnostic variant succeeding does **not** produce **REAL PROVIDER PASS** and does not make the production fix mergeable; the production-shaped request itself must pass on the final exact head.

## Retry/accounting boundary

The worker owns Gemini retries because each external provider attempt must have its own `GeminiCallRecord`, reservation and page-call ordinal. The production adapter therefore constructs the Google GenAI client with SDK-owned HTTP retries disabled. Transient classification still crosses the adapter boundary so the worker can perform a separately accounted retry when policy permits it.

## GitHub Actions smoke

The manual [Gemini Smoke workflow](../.github/workflows/gemini-smoke.yml) uses `workflow_dispatch`; it does not run automatically for pull requests or normal `main` CI.

Configure a GitHub Environment named `gemini-smoke` and add an Environment secret named `GOOGLE_API_KEY`. The workflow grants only `contents: read` and injects the secret only into the configuration preflight and real-provider pytest step.

The workflow distinguishes three outcomes in the GitHub Step Summary:

- **REAL PROVIDER PASS** — the secret was configured, the real provider step executed, and the production-shaped structured output validated successfully;
- **REAL PROVIDER FAILED** — the secret was configured and the real provider step executed but the production-shaped request or validation failed, even if a diagnostic variant was accepted;
- **NOT CONFIGURED / NOT EXECUTED** — the environment did not expose `GOOGLE_API_KEY`, so no provider request was made.

Only **REAL PROVIDER PASS** is provider validation. A successful workflow run whose provider step was skipped because the secret was unavailable must not be treated as proof that Gemini works.

Because GitHub only exposes `workflow_dispatch` after the workflow file exists on the default branch, establish this workflow on `main` before using it as baseline or fix-validation evidence. For branch validation after that point, manually start **Gemini Smoke** and select the exact branch whose head SHA is under review.

## Privacy and cost boundary

The smoke exists to test the external provider contract, not MangaSensei OCR or user data. Keep every prompt synthetic. Do not add real manga bytes, recognized text from a user page, local JMdict data, artifacts containing provider payloads, or environment dumps to this workflow.

A passing production-shaped smoke normally makes one paid provider call. During the bounded #90 diagnostic described above, a rejected production request can trigger additional low-output synthetic calls, up to eight calls total. Run this workflow only as an explicit provider-compatibility or investigation gate rather than ordinary credential-free CI.
