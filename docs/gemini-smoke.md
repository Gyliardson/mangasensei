# Gemini real-provider smoke

MangaSensei keeps ordinary development and CI credential-free. Gemini is optional, and the normal deterministic test suite must pass without a Google API key. The real-provider smoke is a separate, explicitly opt-in assurance layer for the current `GoogleGenAiAdapter` Interactions request.

See the broader [testing strategy](testing.md) for the deterministic CI, full-stack and OCR assurance boundaries.

## Local smoke

The real-provider test is marked `gemini_smoke` and uses the actual `GoogleGenAiAdapter` with a synthetic one-region page-study prompt and the production `GeminiPageAnalysis` structured-output contract. It sends no manga image, user OCR text, local JMdict data or other user content. The call uses one provider attempt while retaining the production Interactions options, including the configured model, `store=False`, low thinking and the normal 16,384 output-token limit.

Run it with:

```sh
uv run pytest tests/test_gemini_smoke.py -m gemini_smoke -q
```

The test reads `GOOGLE_API_KEY` only from the process environment. If the variable is absent or empty, pytest reports the test as skipped with a reason stating that the real provider smoke was not executed. A skipped test is **not** evidence that the provider contract works.

Never commit `.env`, an API key, copied provider headers, or a secret-bearing request dump. Keep credentials in local secret storage or another private environment mechanism.

## Structured-output compatibility boundary

MangaSensei keeps the complete Pydantic response contract as the local validation authority. The Interactions request receives a provider-facing JSON Schema derived from that contract but limited to the JSON Schema subset documented by Gemini structured outputs. In particular, string `minLength`/`maxLength` constraints are omitted from the provider representation while local Pydantic validation still enforces the original string length bounds after the provider returns JSON. Supported array bounds such as `maxItems` remain in the provider schema.

The real-provider smoke deliberately uses the production page-analysis schema so an SDK/model/provider change that rejects this compatibility representation is visible before the optional Gemini path is considered validated.

## GitHub Actions smoke

The manual [Gemini Smoke workflow](../.github/workflows/gemini-smoke.yml) uses `workflow_dispatch`; it does not run automatically for pull requests or normal `main` CI.

Configure a GitHub Environment named `gemini-smoke` and add an Environment secret named `GOOGLE_API_KEY`. The workflow grants only `contents: read` and injects the secret only into the configuration preflight and real-provider pytest step.

The workflow distinguishes three outcomes in the GitHub Step Summary:

- **REAL PROVIDER PASS** — the secret was configured, the real provider step executed, and structured output validated successfully;
- **REAL PROVIDER FAILED** — the secret was configured and the real provider step executed but failed;
- **NOT CONFIGURED / NOT EXECUTED** — the environment did not expose `GOOGLE_API_KEY`, so no provider request was made.

Only **REAL PROVIDER PASS** is provider validation. A successful workflow run whose provider step was skipped because the secret was unavailable must not be treated as proof that Gemini works.

Because GitHub only exposes `workflow_dispatch` after the workflow file exists on the default branch, establish this workflow on `main` before using it as baseline or fix-validation evidence. For branch validation after that point, manually start **Gemini Smoke** and select the exact branch whose head SHA is under review.

## Privacy and cost boundary

The smoke exists to test the external provider contract, not MangaSensei OCR or user data. Keep the prompt synthetic. Do not add real manga bytes, recognized text from a user page, local JMdict data, artifacts containing provider payloads, or environment dumps to this workflow.

The smoke makes one paid provider attempt. It intentionally exercises the production-shaped request rather than a reduced token/schema variant, so run it only as an explicit provider-compatibility gate rather than ordinary credential-free CI.
