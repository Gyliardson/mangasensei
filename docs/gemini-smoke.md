# Gemini real-provider smoke

MangaSensei keeps ordinary development and CI credential-free. Gemini is optional, and the normal deterministic test suite must pass without a Google API key. The real-provider smoke is a separate, explicitly opt-in assurance layer for the current `GoogleGenAiAdapter` Interactions request.

See the broader [testing strategy](testing.md) for the deterministic CI, full-stack and OCR assurance boundaries.

## Local smoke

The real-provider test is marked `gemini_smoke` and uses the actual `GoogleGenAiAdapter` with a tiny synthetic prompt, structured JSON output and Pydantic validation. It sends no manga image, OCR text, lexical data or other user content. The call uses one provider attempt and a 128-token output cap.

Run it with:

```sh
uv run pytest tests/test_gemini_smoke.py -m gemini_smoke -q
```

The test reads `GOOGLE_API_KEY` only from the process environment. If the variable is absent or empty, pytest reports the test as skipped with a reason stating that the real provider smoke was not executed. A skipped test is **not** evidence that the provider contract works.

Never commit `.env`, an API key, copied provider headers, or a secret-bearing request dump. Keep credentials in local secret storage or another private environment mechanism.

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

The smoke exists to test the external provider contract, not MangaSensei OCR or user data. Keep the prompt synthetic and the schema minimal. Do not add real manga bytes, recognized text from a user page, complete production prompts, local JMdict data, artifacts containing provider payloads, or environment dumps to this workflow.

The smoke's bounded one-attempt/128-token configuration is intentionally narrower than the production default. It does not change normal application request limits; it limits only this explicit paid verification call.
