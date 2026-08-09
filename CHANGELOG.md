# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

### Changed

- Linguistic runs now persist the loaded dictionary version and digest instead of a placeholder.
- `models` and `jmdict` CLI commands no longer require database or capability credentials.
- Docker Compose uses a named `jmdict_data` volume so the bootstrap container can write without depending on host directory ownership.
- Worker retryable failures expose a stable public error code instead of internal exception class names.
- `gemini_max_calls_per_page` setting is now wired into the worker instead of being hardcoded.
- CI installs Python dependencies from the committed lockfile with `uv sync --frozen`.
- Documentation uses navigable repository links for contributor, visual-artifact and third-party references.
- Docker/Compose image version metadata is supplied from release tooling instead of a hardcoded application version.
- Normalized JMdict data now uses the `mangasensei-jmdict-v2` form contract and reproducible manifest metadata derived from the checksum-pinned source artifact.
- Browser assurance now labels the fast API-mocked Playwright suite separately from the required full-stack critical-flow E2E.
- Page and status responses now expose `resultAvailable` separately from the latest analysis-attempt status.

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
- Reader vocabulary now exposes deterministic local JMdict entries even when Gemini is disabled or omits a vocabulary link; contextual Gemini fields remain optional enrichment.
- Reprocessing no longer hides a previously completed study result while a replacement is pending or after that replacement fails; only a newer completed result replaces it.

## [0.1.0] - 2026-08-07

### Added

- Initial MangaSensei MVP implementation.

[Unreleased]: https://github.com/gyliardson/mangasensei/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gyliardson/mangasensei/releases/tag/v0.1.0
