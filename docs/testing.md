# Testing strategy

MangaSensei uses separate validation layers so fast deterministic feedback does not get confused with heavier product-boundary assurance.

## Fast deterministic CI

The normal [CI workflow](../.github/workflows/ci.yml) runs on pull requests and `main`. Backend worker integration tests use deterministic OCR fixtures where appropriate, while frontend Playwright tests exercise browser behavior without loading the real OCR checkpoints. These tests remain the fast required gates for ordinary changes.

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

The real-model smoke is not a browser-to-worker end-to-end test. Full-stack assurance should separately exercise browser -> FastAPI -> PostgreSQL/job queue -> worker -> persisted result -> protected browser read, while keeping the heavyweight OCR boundary deterministic when appropriate. That distinct critical-flow gate is tracked separately so neither test layer overstates what it validates.
