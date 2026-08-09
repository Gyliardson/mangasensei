# Version Matrix

Verified on 2026-08-09 using official documentation, package registries and reviewed artifact manifests.

| Area | Component | Version | Notes |
| --- | --- | --- | --- |
| Runtime | Python | 3.11 | Shared backend, worker and integrated OCR runtime |
| Runtime | Node.js | 24 LTS target | Node 22.12+ is supported for local tooling |
| API | FastAPI | 0.141.1 | Pydantic v2 only |
| Validation | Pydantic | 2.13.4 | Strict immutable domain contracts |
| ORM | SQLAlchemy | 2.0.51 | Async PostgreSQL dialect |
| Migrations | Alembic | 1.19.0 | Reversible initial schema |
| Database | PostgreSQL | 18.4 | Official container image |
| Gemini | google-genai | 2.17.0 | Structured JSON output through current SDK |
| UI | React | 19.2.8 | SPA |
| Language | TypeScript | 7.0.2 | CLI type checking only |
| Build | Vite | 8.2.1 | Node 22.12+ required |
| Styles | Tailwind CSS | 4.3.3 | Vite plugin; no Tailwind v3 syntax |
| Components | shadcn CLI | 4.16.2 | Tailwind v4 compatible |
| Unit tests | Vitest | 4.1.10 | jsdom environment |
| E2E | Playwright | 1.62.1 | Desktop and mobile projects |
| Dictionary data | jmdict-simplified | 3.6.2+20260803141815 | English source pinned by checksum; normalized with `mangasensei-jmdict-v3` using the runtime canonical form key while preserving reading/spelling and sense restrictions |
| OCR source | manga-image-translator | 95227a2bb0fd306cd4f0c104d57284026f991b3a | Vendored OCR subset |
| OCR image runtime | opencv-python-headless | 5.0.0.93 | Headless Python wheel; adopted after a same-code 12-page A/B against 4.14.0.94 |

Python packages are locked by `uv.lock`. JavaScript packages are locked by
`package-lock.json`. Model artifacts use a separate checksum manifest because
they cannot be redistributed with the application.

The normalized JMdict artifact is derived locally from the reviewed source ZIP and is not
hand-edited. When the converter contract changes, refresh its reviewed derived metadata with
`uv run python scripts/update_jmdict_manifest.py`; use `--check` to verify that the manifest
matches the current converter and pinned source without modifying it.

## Application release version

`pyproject.toml` remains the authoritative application release version. The supported
`scripts/version.py set X.Y.Z` command updates the tracked Python and npm mirrors, including
`mangasensei.__version__` and `frontend/package.json`. FastAPI/OpenAPI metadata and `/health`
derive from `mangasensei.__version__`; the browser footer derives from the frontend package
version. They are therefore runtime/build-time consumers of synchronized mirrors rather than
additional literals that must be edited separately. `scripts/version.py check` continues to
validate the tracked mirrors before CI and release workflows proceed.
