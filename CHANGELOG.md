# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Licensed real-manga OCR fixture corpus with documented provenance and integrity metadata.
- Reviewed real-model OCR regressions for known-good short vertical dialogue on licensed manga pages.
- Layered licensed OCR assurance with manifest integrity checks, repeated short-text inference and a deeper full-corpus catastrophic-output guard.
- Verified JMdict bootstrap command and local dictionary manifest.
- Root GPL-3.0-only license file and third-party notices.
- Expanded multilingual portfolio documentation with architecture, setup and API references.
- Open-source community files: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue and pull request templates, and a GitHub Actions CI workflow.
- Brazilian Portuguese, Japanese and Spanish contributor, security and code-of-conduct guides, plus synchronized multilingual README presentation and project activity links.
- Distribution CI that builds and clean-installs the Python wheel, plus a production Docker build gate.
- Version consistency tooling, repository-local Markdown link validation, Dependabot configuration, and tagged GitHub Release automation.
- Privacy-safe worker failure diagnostics with pipeline stage, retry correlation, exception type and bounded source-location context for operator logs.
- Reviewed real-OCR smoke automation with checksum-verified model downloads, scheduled/manual validation and a release-publishing gate.
- Deterministic full-stack browser gate covering the real FastAPI, PostgreSQL, queue/worker, persistence, capability-protected reads and frontend polling path with only OCR inference doubled.
- Explicit SHA-pinned CodeQL advanced workflow for Actions, JavaScript/TypeScript and Python on pull requests, `main` pushes and weekly scans.
- Reader-local furigana display preferences for Hiragana, canonical Katakana and hidden ruby, persisted safely in browser storage.
- Adaptive reader fit and zoom controls for portrait and landscape pages, with local preferences and contained pan/scroll behavior.
- Explicit Japanese-content study-language backend contract for `pt-BR` and `en`, including persisted effective-language metadata, structured Gemini language input, backward-compatible `pt-BR` migration, and language-only reprocessing that reuses completed OCR and Japanese linguistic analysis.

### Changed

- Linguistic runs now persist the loaded dictionary version and digest instead of a placeholder.
- `models` and `jmdict` CLI commands no longer require database or capability credentials.
- Docker Compose uses a named `jmdict_data` volume so the bootstrap container can write without depending on host directory ownership.
- Worker retryable failures expose a stable public error code instead of internal exception class names.
- `gemini_max_calls_per_page` setting is now wired into the worker instead of being hardcoded.
- CI installs Python dependencies from the committed lockfile with `uv sync --frozen`.
- Documentation uses navigable repository links for contributor, visual-artifact and third-party references.
- Docker/Compose image version metadata is supplied from release tooling instead of a hardcoded application version.
- Normalized JMdict data now uses the `mangasensei-jmdict-v3` canonical form contract and reproducible manifest metadata derived from the checksum-pinned source artifact.
- Browser assurance now labels the fast API-mocked Playwright suite separately from the required full-stack critical-flow E2E.
- Page and status responses now expose `resultAvailable` separately from the latest analysis-attempt status.
- Tagged release publishing now requires the reusable JMdict source-to-consumer contract plus clean Compose prerequisite, API-readiness and worker-readiness validation on the exact tag SHA.
- Reader page-presentation controls are now manga-scoped and sticky while normal vertical reading uses the document; mobile presents the comfortable baseline as width-fit without rewriting persisted preferences.

### Fixed

- Frontend unit coverage now excludes the Playwright `e2e` directory from Vitest collection.
- `npm run e2e` root script added to match the documented quality gates.
- Capability pepper placeholders are rejected; `.env.example` documents secret generation.
- Dev-only database credential placeholder documented in `backend/alembic.ini`.
- One-shot Compose services no longer run the API healthcheck.
- Integration tests truncate the full `mangasensei` schema so they isolate correctly against a shared dev database.
- The required `mangasensei.ocr.models` package is tracked in Git; root-only runtime model ignores no longer hide Python source files from clean clones.
- Local-only worker processing no longer fails database offset constraints when Sudachi normalization emits a zero-width morpheme.
- Docker runtime roles now receive only their required database, capability and Gemini secrets; DB-only roles no longer require capability peppers.
- Local JMdict lookup now respects kana-to-kanji and sense-to-form restrictions instead of reconstructing invalid Cartesian-product associations.
- JMdict bootstrap now canonicalizes script-equivalent runtime form keys and preserves their combined meanings, so checksum-valid generated data cannot block the worker on duplicate normalized forms.
- Reader vocabulary now exposes deterministic local JMdict entries even when Gemini is disabled or omits a vocabulary link; contextual Gemini fields remain optional enrichment.
- Reprocessing no longer hides a previously completed study result while a replacement is pending or after that replacement fails; only a newer completed result replaces it.
- Expired worker leases and 24-hour retention now reconcile abandoned Gemini reservations before ownership or page data is discarded; unsent reservations are released and uncertain sent calls are conservatively charged exactly once.
- Gemini enrichment now receives and validates minimal region-scoped local vocabulary candidates instead of a page-global list of opaque JMdict identifiers.
- Gemini-enabled jobs now require exactly one structured analysis per non-empty OCR region; incomplete or duplicate responses retry without partial Gemini persistence, while zero-region OCR completes without an external call.
- CodeQL pull-request analysis no longer relies on default-setup configuration matching that could skip Dependabot heads or produce neutral missing-configuration summaries.
- OCR runs now persist provenance supplied by the engine from the verified model manifest and effective configuration instead of worker-side literals or zero-region fallbacks.
- Normal worker logging no longer emits recognized manga text from the vendored OCR recognizer at INFO level.
- API metadata, `/health` and the frontend footer now derive the synchronized package/workspace release version instead of independent hardcoded literals.
- Reader furigana now suppresses kana-only script-equivalent annotations and presents useful canonical katakana readings in learner-facing hiragana without altering API token data.
- The upload drop target now accepts the advertised single-file drag-and-drop gesture while rejecting multi-file drops explicitly; server-side image validation remains authoritative.
- The processing-screen action now states that it only stops client-side observation; queued/running backend analysis and normal 24-hour retention continue unchanged.
- The upload landing page now masks the section index over the card border and anchors the next-step block to the same workspace axis on desktop and mobile layouts.
- OCR regions now follow deterministic manga page tiers from top to bottom and right to left within each tier instead of globally prioritizing vertical text by X position.

## [0.1.0] - 2026-08-07

### Added

- Initial MangaSensei MVP implementation.

[Unreleased]: https://github.com/gyliardson/mangasensei/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gyliardson/mangasensei/releases/tag/v0.1.0
