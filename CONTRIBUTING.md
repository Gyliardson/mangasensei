# Contributing to MangaSensei

Languages: [English](CONTRIBUTING.md) | [Português](CONTRIBUTING.pt-BR.md) | [日本語](CONTRIBUTING.ja.md) | [Español](CONTRIBUTING.es.md)

Thanks for your interest in MangaSensei. Contributions, issues and pull requests are welcome in **English, Português, 日本語 or Español**. Before participating, please read the sections below. By participating you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Code of Conduct

Please read the [Code of Conduct](CODE_OF_CONDUCT.md). Harassment and excluding behavior are not welcome. If you see a violation, follow the private reporting guidance described there.

## Security

If you find a security vulnerability, do **not** open a public issue. Follow the responsible disclosure process in the [Security Policy](SECURITY.md).

## Getting Started

1. Read the main [README](README.md), also available in [Português](README.pt-BR.md), [日本語](README.ja.md) and [Español](README.es.md).
2. Make sure you can run the relevant local quality gates before changing code.
3. Check existing issues, discussions and pull requests to avoid duplicate work.
4. For non-trivial changes, open a discussion or issue first to agree on the approach.

## Development Workflow

We use a protected `main` branch and focused pull requests:

- Fork the repository if you are an external contributor, or create a focused branch if you have write access.
- Keep changes small and reviewable. Prefer several focused pull requests over one giant one.
- Update your branch with the latest `main` before requesting a final review.
- Open a pull request against `main` and fill in the PR template.
- Required CI checks and unresolved review conversations must be cleared before merge.

### Branch naming

Use a short descriptive name, for example:

```text
feat/reading-order
fix/idempotency-conflict
docs/jmdict-license
```

### Conventional commits

Commit messages and pull request titles follow [Conventional Commits]:

```text
<type>(<scope>): <description>
```

Examples:

```text
feat(worker): persist dictionary version and digest
fix(cli): allow jmdict/models verify without database credentials
docs: add multilingual community guidance
test(runner): cover public error code mapping
```

Common types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`, `build`, `style`, `revert`.

The repository uses squash merge, so the reviewed pull request title becomes the final commit title on `main`.

## Structure and Conventions

```text
backend/   Python API, worker, migrations, OCR and linguistics modules
frontend/  React SPA, reader components and Playwright tests
docs/      Version notes and visual artifacts
tests/     Backend unit and integration tests
```

Guidelines:

- Backend: keep modules focused, use type hints, preserve strict typing and maintain the configured coverage requirement.
- Frontend: follow existing React/TypeScript patterns and keep accessibility in mind.
- Never commit generated data, model weights, `.env` files, credentials or build artifacts.
- Keep [`.gitignore`](.gitignore) and [`.dockerignore`](.dockerignore) aligned when runtime artifact locations change.
- Preserve the local-first design and the original uploaded image.

## Local Quality Gates

Before pushing, run the gates relevant to your change. The current GitHub Actions workflows are the source of truth for required CI.

```powershell
# repository consistency
.\.venv\Scripts\python.exe scripts/version.py check
.\.venv\Scripts\python.exe scripts/check_markdown_links.py

# backend
.\.venv\Scripts\python.exe -m ruff check backend/src tests scripts
.\.venv\Scripts\python.exe -m mypy backend/src
$env:MANGASENSEI_TEST_DATABASE_URL='postgresql+psycopg://mangasensei:mangasensei@127.0.0.1:55432/mangasensei'
.\.venv\Scripts\python.exe -m pytest --cov

# frontend
npm run lint
npm run typecheck
npm run test:coverage
npm run build
npm run e2e
```

Notes:

- Backend integration tests need PostgreSQL. Use the local dev database or the Docker Compose PostgreSQL service.
- The optional OCR smoke test loads real model weights and is skipped by default. Set `MANGASENSEI_RUN_OCR_SMOKE=1` and `MANGASENSEI_MODEL_CACHE` to enable it.
- Frontend end-to-end tests launch a Playwright web server automatically.
- CI also builds the Python distribution, clean-installs the wheel, builds the production Docker image and runs security checks.

## Documentation and Translations

English files are the canonical integration files recognized by GitHub, but contributor-facing documentation is maintained in English, Brazilian Portuguese, Japanese and Spanish.

When shared README or community guidance changes:

- Update all affected language versions in the same pull request when practical.
- Keep commands, paths, version numbers and technical contracts identical across translations.
- Do not translate `LICENSE`, `CHANGELOG.md`, `THIRD_PARTY_NOTICES.md`, manifests or workflow files into parallel sources of truth.
- Reader-facing file references should use navigable Markdown links.

## Versioning and Releases

The project uses [Semantic Versioning](https://semver.org/) and keeps curated release notes in the [changelog](CHANGELOG.md). The Python project version in [`pyproject.toml`](pyproject.toml) is authoritative; repository tooling mirrors it where needed.

Do not edit version mirrors manually. If [`scripts/version.py`](scripts/version.py) remains the current version tool, use:

```powershell
.\.venv\Scripts\python.exe scripts/version.py set 0.2.0
```

The command updates mechanical version mirrors. It deliberately does **not** write release notes. Promote and edit the relevant `[Unreleased]` entries in [`CHANGELOG.md`](CHANGELOG.md) manually.

After the release commit has passed CI and is merged to `main`, a matching `vX.Y.Z` tag triggers the [release workflow](.github/workflows/release.yml). Maintainers decide when a release is ready; contributors should not create release tags as part of normal pull requests.

## Reporting Issues

Use the issue templates when possible. You may write reports in any of the four supported documentation languages.

- **Bug reports**: include the version or commit, operating system, reproduction steps and relevant logs.
- **Feature requests**: describe the motivation, expected behavior and any privacy/API/storage impact.

## Pull Requests

Fill in the PR template. A PR should:

- Describe what it changes and why.
- Reference the related issue when applicable (for example `Closes #123`).
- List the tests or checks that were run.
- Pass the required quality gates.
- Update affected documentation.
- Avoid unrelated cleanup.

Maintainers will review functionality, security, compatibility and project fit. Changes may be requested, a large contribution may be split into smaller pull requests, or an out-of-scope proposal may be closed with an explanation.

## License

By contributing you agree that your contributions are licensed under the same terms as the project (`GPL-3.0-only`). See the project [license](LICENSE).

[Conventional Commits]: https://www.conventionalcommits.org/en/v1.0.0/
