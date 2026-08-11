# Version Matrix

Verified on 2026-08-11 using official documentation, package registries, selected locked distributions and reviewed artifact manifests.

| Area | Component | Version | Notes |
| --- | --- | --- | --- |
| Runtime | Python | 3.11 | Shared backend, worker and integrated OCR runtime |
| Runtime | Node.js | 24 LTS target | Node 22.12+ is supported for local tooling |
| API | FastAPI | 0.141.1 | Pydantic v2 only |
| Validation | Pydantic | 2.13.4 | Strict immutable domain contracts |
| ORM | SQLAlchemy | 2.0.51 | Async PostgreSQL dialect |
| Migrations | Alembic | 1.19.0 | Reversible initial schema |
| Database | PostgreSQL | 18.4 | Official container image |
| PDF wrapper | pypdfium2 | 5.12.1 | Pinned Python wrapper; production startup rejects a different helper/runtime identity |
| PDF renderer | PDFium | 152.0.7947.0 (build 7947) | Bundled by the locked `pypdfium2_raw` wheel; standard build with no V8/XFA flags; no system-PDFium fallback is accepted |
| Gemini | google-genai | 2.17.0 | Structured JSON output through current SDK |
| UI | React | 19.2.8 | SPA |
| Language | TypeScript | 7.0.2 | CLI type checking only |
| Build | Vite | 8.2.1 | Node 22.12+ required |
| Styles | Tailwind CSS | 4.3.3 | Vite plugin; no Tailwind v3 syntax |
| Components | shadcn CLI | 4.16.2 | Tailwind v4 compatible |
| Unit tests | Vitest | 4.1.10 | jsdom environment |
| E2E | Playwright | 1.62.1 | Desktop and mobile projects |
| Dictionary data | jmdict-simplified | 3.6.2+20260803141815 | Reviewed `en`/`eng` and `de`/`ger` packs share one pinned source snapshot; English remains the default runtime pack; both normalize with `mangasensei-jmdict-v3` while preserving reading/spelling and sense restrictions |
| OCR source | manga-image-translator | 95227a2bb0fd306cd4f0c104d57284026f991b3a | Vendored OCR subset |
| OCR image runtime | opencv-python-headless | 5.0.0.93 | Headless Python wheel; adopted after a same-code 12-page A/B against 4.14.0.94 |

Python packages are locked by `uv.lock`. JavaScript packages are locked by
`package-lock.json`. Model artifacts use a separate checksum manifest because
they cannot be redistributed with the application.

The supported PDF renderer is deliberately narrower than the wrapper's generic installation options. The application requires the locked binary distribution to resolve `pypdfium2_raw/libpdfium.so` from the installed wheel, requires PDFium build `7947`, and rejects V8/XFA-enabled builds. This prevents an unnoticed system-PDFium or source-build substitution from changing the persisted `pdfium-raster-v1` contract. The selected Linux x86_64 wheel's native dependencies were inspected in CI and resolve only to the platform's standard glibc/POSIX libraries; bundled PDFium third-party notices are recorded in [Third-Party Notices](../THIRD_PARTY_NOTICES.md).

Reviewed JMdict language packs are registered in
[`backend/src/mangasensei/linguistics/jmdict_packs.json`](../backend/src/mangasensei/linguistics/jmdict_packs.json), with independent source and normalized integrity metadata in each pack manifest. All packs in the registry must declare the same reviewed source snapshot. English remains the configured worker/runtime dictionary in this infrastructure slice; German is selected explicitly only through the bootstrap/verification CLI and is not eagerly loaded.

Normalized JMdict artifacts are derived locally from the exact reviewed source ZIPs and are not
hand-edited. When the converter contract or reviewed pack metadata changes, refresh derived
metadata with `uv run python scripts/update_jmdict_manifest.py`; use `--check` to verify all
registered packs without modifying them, or `--language en` / `--language de` to select a
specific reviewed pack. See [Reviewed JMdict language packs](jmdict-packs.md) for the current
bootstrap boundary and [JMdict pack load measurement methodology](jmdict-pack-measurement.md)
for the non-gating RSS/startup evidence method.

## Application release version

`pyproject.toml` remains the authoritative application release version. The supported
`scripts/version.py set X.Y.Z` command updates the tracked Python and npm mirrors, including
`mangasensei.__version__` and `frontend/package.json`. FastAPI/OpenAPI metadata and `/health`
derive from `mangasensei.__version__`; the browser footer derives from the frontend package
version. They are therefore runtime/build-time consumers of synchronized mirrors rather than
additional literals that must be edited separately. `scripts/version.py check` continues to
validate the tracked mirrors before CI and release workflows proceed.
