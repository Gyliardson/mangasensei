# Testing strategy

MangaSensei uses separate validation layers so fast deterministic feedback does not get confused with heavier product-boundary assurance.

## Fast deterministic CI

The normal [CI workflow](../.github/workflows/ci.yml) runs on pull requests and `main`. Backend worker integration tests use deterministic OCR fixtures where appropriate. The existing Playwright suite remains a fast **mocked browser E2E** layer for desktop/mobile interaction, keyboard behavior and accessibility; it intercepts MangaSensei API requests and therefore does not claim full-stack assurance.

The same required Frontend CI job also runs the separate [full-stack critical-flow suite](../frontend/e2e-fullstack/critical-flow.spec.ts) described below. A failure in either browser layer fails the required Frontend quality check.

## CodeQL security analysis

The repository uses the explicit [CodeQL workflow](../.github/workflows/codeql.yml) rather than GitHub CodeQL default setup. It analyzes `actions`, `javascript-typescript` and `python` independently on pull requests targeting `main`, pushes to `main`, and a weekly schedule. Each language has a stable `/language:<identifier>` category so pull-request results can be matched to the same configuration on the default branch.

The advanced workflow is intentional for Dependabot coverage. GitHub gives Dependabot-triggered workflows restricted permissions, but code scanning permits result uploads when the analysis is triggered by the `pull_request` event. The workflow therefore uses `pull_request` for proposed changes and `push` only for `main`; it does not use `pull_request_target`, repository secrets, or elevated write permissions unrelated to code scanning.

The CodeQL action and checkout action are pinned to reviewed commit SHAs. The job grants only `actions: read`, `contents: read`, `packages: read`, and `security-events: write`; GitHub applies its stricter Dependabot token policy when applicable. All three `Analyze (...)` jobs must complete successfully for the exact pull-request head before a change is considered security-validated. A neutral comparison summary is not a substitute for the language analysis jobs themselves.

When changing the CodeQL workflow or action pin, validate the workflow on GitHub. A future Dependabot pull request must also show the three advanced CodeQL language jobs; if one is absent or cannot upload results, treat #19's security assurance as regressed rather than bypassing the check.

## JMdict data contract

The [JMdict Data Contract workflow](../.github/workflows/jmdict-contract.yml) protects the deterministic dictionary boundary when its converter, loader, manifest or updater changes. It downloads the exact source ZIP pinned by the [JMdict manifest](../backend/src/mangasensei/linguistics/jmdict_manifest.json), verifies the reviewed size and SHA-256, regenerates the normalized representation, and requires the derived SHA-256, byte size, entry count and converter version to match the committed manifest metadata.

The normalized dictionary itself remains a locally derived artifact and is not committed or uploaded by the workflow. The `mangasensei-jmdict-v2` contract preserves valid spelling/reading forms and form-specific meanings instead of reconstructing unrestricted Cartesian products.

Maintainers can reproduce the metadata update with:

```sh
uv run python scripts/update_jmdict_manifest.py
```

After committing the derived manifest values, verify them without modifying the file with:

```sh
uv run python scripts/update_jmdict_manifest.py --check
```

Both commands verify the manifest-pinned source before accepting derived metadata. The workflow runs automatically only for relevant JMdict contract changes and can also be started manually.

## Real OCR model smoke

The [OCR Smoke workflow](../.github/workflows/ocr-smoke.yml) exercises the production `MangaImageTranslatorEngine` on CPU with the reviewed model artifacts from the [OCR model manifest](../backend/src/mangasensei/ocr/models/manifest.json).

The workflow:

- installs the committed OCR dependency set with `uv sync --frozen --extra ocr`;
- obtains model files only through `mangasensei models download`;
- re-verifies exact artifact size and SHA-256 with `mangasensei models verify` before inference;
- sets `MANGASENSEI_RUN_OCR_SMOKE=1` and runs only `tests/test_ocr_smoke.py`;
- uses deterministic synthetic image input, so failures do not require or expose user manga content;
- does not upload or redistribute the OCR model weights through workflow artifacts or dependency caches.

> **Note on Real Manga Fixtures:** The repository now contains a small corpus of licensed, real-manga OCR fixtures in `tests/fixtures/ocr/real_manga/` to provide future validation and regressions against real-world complexities. However, the current smoke test pipeline continues to use synthetic input until those fixtures are fully integrated in a separate task. This separates the introduction and provenance of the data from the behavioral changes in the tests.

Model weights are deliberately downloaded fresh on GitHub-hosted runners while their redistribution status remains pending review. The project manifest and integrity checks remain the source of truth for the exact files loaded by the smoke.

### Automated policy

The heavy smoke stays separate from ordinary CI cost, but runs automatically when its boundary is relevant:

- pull requests and `main` changes that touch the OCR implementation/model files, `tests/test_ocr_smoke.py`, `pyproject.toml`, `uv.lock`, or the smoke workflow itself;
- once per week from the default branch;
- on explicit maintainer `workflow_dispatch` runs;
- every tagged release, because the [release workflow](../.github/workflows/release.yml) calls the same reusable OCR smoke and will not publish unless it succeeds.

If OCR, model, or Python dependency compatibility changed since the last reviewed validation, run **OCR Smoke** on the exact release-candidate SHA before creating the release tag. The tag workflow intentionally runs the same smoke again before publishing.

### Run locally

First install the OCR dependency extra. Then download and verify the reviewed model artifacts before enabling the smoke.

PowerShell:

```powershell
py -3.11 -m uv sync --extra ocr
$env:MANGASENSEI_MODEL_CACHE='var/models'
.\.venv\Scripts\mangasensei.exe models download
.\.venv\Scripts\mangasensei.exe models verify
$env:MANGASENSEI_RUN_OCR_SMOKE='1'
.\.venv\Scripts\python.exe -m pytest tests/test_ocr_smoke.py -m ocr_smoke -vv --maxfail=1
```

POSIX shell:

```sh
uv sync --extra ocr
export MANGASENSEI_MODEL_CACHE=var/models
.venv/bin/mangasensei models download
.venv/bin/mangasensei models verify
export MANGASENSEI_RUN_OCR_SMOKE=1
.venv/bin/python -m pytest tests/test_ocr_smoke.py -m ocr_smoke -vv --maxfail=1
```

A model-load, vendored API, PyTorch/OpenCV, or inference incompatibility must fail this smoke rather than being treated as proof that the fast fixture-backed tests cover the real model boundary.

## Full-stack critical flow

The required Frontend CI job contains a separate **Full-stack critical-flow E2E** step. It runs [Playwright with its own configuration](../frontend/playwright.fullstack.config.ts) against FastAPI serving the production frontend build on `127.0.0.1:8000`; the browser does not intercept or synthesize MangaSensei `/api/v1/**` responses.

The full-stack harness uses:

- the real current Alembic migrations and a real PostgreSQL service;
- the real FastAPI upload, status, protected-page and protected-image endpoints;
- capability tokens returned by the real upload response and consumed by the real frontend client;
- the real queue claim/state-transition and `Worker` implementation;
- real local filesystem storage, Sudachi tokenization, normalized JMdict loading and linguistic persistence;
- Gemini disabled, validating the privacy-first local baseline;
- a deterministic OCR engine double only at the OCR inference boundary, so heavy model loading stays in the separate Real OCR model smoke.

The worker fixture deliberately keeps OCR in progress long enough for browser polling to observe a non-terminal real job state before `completed`. The final browser assertions require persisted `猫です` output, local JMdict vocabulary, the no-Gemini contextual fallback, a successful protected original-image read, and a successful protected page-result read. If migrations, capabilities, queue orchestration, persistence, the API/frontend contract, or worker completion breaks, this required Frontend check fails.

The full-stack Playwright command is:

```sh
npm run e2e:fullstack
```

It assumes PostgreSQL has been migrated and the API plus deterministic worker harness are already running; the CI workflow performs that orchestration. This test is intentionally distinct from both the fast mocked browser suite and the heavyweight real-model OCR smoke so each layer states exactly what it validates.
