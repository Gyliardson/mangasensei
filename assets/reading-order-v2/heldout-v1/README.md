# MangaSensei Reading Order v2 held-out corpus v1

`mangasensei-reading-order-heldout-v2` version `1.0.0` is the frozen project-authored held-out qualification corpus for the `reading-order-v2-experiment-spec-v1` methodology. It is not calibration material and this corpus directory contains no Reading Order v2 candidate output or quality conclusion.

## Separation contract

- `source/Hxx.svg` is deterministic project-authored page art. It contains no semantic text, fonts, external images, or network references.
- `images/Hxx.png` is the canonical 1440×2048 RGB render generated only by [`scripts/reading_order_v2/render.mjs`](../../../scripts/reading_order_v2/render.mjs).
- `inputs/Hxx.json` is arm-visible input only: frozen post-merge `TextBlock`-equivalent geometry, stable region IDs/source indexes, and angles.
- `annotations/Hxx.json` is scorer/fixture-validation GT only and must never be supplied to an arm.
- `provenance/toolchain.json` records the non-private rendering environment.
- `manifest.json` cryptographically freezes the corpus inventory after rendering and validation.

The visible glyph-like bars are neutral geometry only. They do not encode reading order, labels, semantic Japanese/English strings, confidence, panel GT, orientation GT, assignment GT, or qualification slices.

## Anti-contamination rule

H01–H16 source geometry, arm-visible fixtures, reading-order GT, qualification pairs, panel GT, orientation expectations, and assignment expectations must be authored and reviewed without candidate-arm output. Public Demo quality annotations/results, known calibration failures, historical candidate benchmark outputs, PP/current48 integrated outputs, Baberu output, OCR output, and Black Jack evidence are not authoring inputs for this corpus.

Once qualification has ever executed against version `1.0.0`, any source, image, input, annotation, provenance, license, or corpus-documentation change requires a new corpus version; it must not be silently re-frozen in place.

## Regeneration and validation

Use the repository-locked environments. The repository CI convention installs Node dependencies with `npm ci` and the Playwright Chromium bundle from the locked package. A reproducible local sequence is:

```bash
npm ci
(cd frontend && npx --no-install playwright install --with-deps chromium)
uv sync --frozen --extra ocr
uv run python -m scripts.reading_order_v2.validate_design
node scripts/reading_order_v2/render.mjs
uv run python -m scripts.reading_order_v2.freeze
uv run python -m scripts.reading_order_v2.validate_corpus
uv run pytest tests/reading_order_v2/test_heldout_corpus.py tests/reading_order_v2/test_heldout_fixture_orientation.py tests/reading_order_v2
```

Do not run `scripts/reading_order_v2/run_arm.py` or `scripts/reading_order_v2/run_heldout.py` as part of authoring/freeze.

## License

The corpus-specific material in this directory is licensed under [CC BY 4.0](LICENSE); see [NOTICE.md](NOTICE.md) for attribution and provenance. MangaSensei source code remains GPL-3.0-only.
