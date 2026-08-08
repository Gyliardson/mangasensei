# Contributing to MangaSensei

Thanks for your interest in MangaSensei. This project is open for everyone. Before
opening an issue or a pull request, please read the sections below. By participating
you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Code of Conduct

Please read `CODE_OF_CONDUCT.md`. Harassment and excluding behavior are not welcome.
If you see a violation, contact the maintainers privately (see `SECURITY.md` for
reporting paths).

## Security

If you find a security vulnerability, do **not** open a public issue. Follow the
responsible disclosure process in `SECURITY.md`.

## Getting Started

1. Read the main `README.md` (also available in `pt-BR`, `ja`, `es`).
2. Make sure you can run the full local quality gates from the README before changing code.
3. Check existing issues and pull requests to avoid duplicate work.
4. For non-trivial changes, open a discussion or issue first to agree on the approach.

## Development Workflow

We follow a simplified trunk-based flow built around `main`:

- Fork the repository (for external contributors) or create your own branch.
- Keep changes small and reviewable. Prefer several focused pull requests over one giant one.
- Rebase your branch on the latest `main` before requesting a review.
- Open a pull request against `main` and fill in the PR template.

### Branch naming

A short descriptive name, for example:

```text
feat/reading-order
fix/idempotency-conflict
docs/jmDict-license
```

### Conventional commits

Commit messages follow [Conventional Commits]:

```text
<type>(<scope>): <description>
```

Examples:

```text
feat(worker): persist dictionary version and digest
fix(cli): allow jmdict/models verify without database credentials
docs: add CONTRIBUTING and community files
test(runner): cover public error code mapping
```

Common types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`,
`build`, `style`, `revert`.

## Structure and Conventions

```text
backend/   Python API, worker, migrations, OCR and linguistics modules
frontend/  React SPA, reader components and Playwright tests
docs/      Version notes and generated visual artifacts
tests/     Backend unit and integration tests
```

Guidelines:

- Backend: single responsibility per file (small focused modules), 80%+ test
  coverage, type hints, no mix of old/new framework idioms.
- Frontend: follow the existing React/TypeScript patterns; keep accessibility in mind.
- Never commit generated data, model weights, `.env` files, or build artifacts.
  The `.gitignore` and `.dockerignore` already cover these — please keep them updated.

## Local Quality Gates

Before pushing, run the same gates that CI runs:

```powershell
# backend
.\.venv\Scripts\python.exe -m ruff check backend/src tests
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

- Backend integration tests need a PostgreSQL instance. Use the local dev database
  (see `MANGASENSEI_TEST_DATABASE_URL` above) or the `docker compose` PostgreSQL.
- The optional OCR smoke test loads real model weights and is skipped by default:
  set `MANGASENSEI_RUN_OCR_SMOKE=1` and `MANGASENSEI_MODEL_CACHE` to enable it.
- Frontend end-to-end tests launch a Playwright web server automatically.

## Reporting Issues

Use the issue templates when possible.

- **Bug reports**: include the version, operating system, reproduction steps, and
  any relevant logs.
- **Feature requests**: describe the motivation and the expected behavior.

## Pull Requests

Fill in the PR template. A PR should:

- Describe what it changes and why.
- Reference the related issue (e.g. `Closes #123`).
- List the tests that were run.
- Pass all quality gates.
- Not introduce unrelated changes.

Maintainers will review and may request changes. Please be patient and responsive.

## License

By contributing you agree that your contributions are licensed under the same terms
as the project (`GPL-3.0-only`). See `LICENSE`.

[Conventional Commits]: https://www.conventionalcommits.org/en/v1.0.0/
