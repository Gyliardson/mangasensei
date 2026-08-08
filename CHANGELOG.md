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

### Changed

- Linguistic runs now persist the loaded dictionary version and digest instead of a placeholder.
- `models` and `jmdict` CLI commands no longer require database or capability credentials.
- Docker Compose uses a named `jmdict_data` volume so the bootstrap container can write without depending on host directory ownership.
- Worker retryable failures expose a stable public error code instead of internal exception class names.
- `gemini_max_calls_per_page` setting is now wired into the worker instead of being hardcoded.

### Fixed

- Frontend unit coverage now excludes the Playwright `e2e` directory from Vitest collection.
- `npm run e2e` root script added to match the documented quality gates.
- Capability pepper placeholders are rejected; `.env.example` documents secret generation.
- Dev-only database credential placeholder documented in `backend/alembic.ini`.
- One-shot Compose services no longer run the API healthcheck.
- Integration tests truncate the full `mangasensei` schema so they isolate correctly against a shared dev database.

## [0.1.0] - 2026-08-07

### Added

- Initial MangaSensei MVP implementation.

[Unreleased]: https://github.com/gyliardson/mangasensei/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/gyliardson/mangasensei/releases/tag/v0.1.0
