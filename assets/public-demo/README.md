# MangaSensei Public Demo Corpus v1

This directory is the canonical MangaSensei-owned public demo and ground-truth corpus. It is intentionally separate from the licensed real-world *Give My Regards to Black Jack* OCR pressure fixtures.

The corpus contains four original manga-like pages:

| Page | Split | Primary purpose |
| --- | --- | --- |
| `msdemo-001-station` | `public-demo` | Hero/desktop presentation, vertical dialogue, signage, furigana |
| `msdemo-002-library` | `public-validation` | Four-panel right-to-left manga flow and dense reading order |
| `msdemo-003-laboratory` | `public-validation` | Similar glyphs, bōten/presentation marks and non-text graphical controls |
| `msdemo-004-rain` | `public-demo` | Mobile/social presentation, SFX, thought text and environmental signage |

## Canonical artifacts

- `source/*.svg`: editable project-authored vector source and deterministic Japanese text layers.
- `images/*.png`: canonical 1440×2048 RGB renders.
- `annotations/*.json`: source-intent ground truth. OCR output is never used to author or correct these files.
- `manifest.json`: complete inventory, provenance and SHA-256 integrity metadata.
- `provenance/fonts.json`: exact non-vendored font acquisition contract.
- `provenance/toolchain.json`: canonical rendering toolchain.

Use [`scripts/public_demo/validate.py`](../../scripts/public_demo/validate.py) to verify the frozen contract. The benchmark evaluator is intentionally outside v1 scope.

See [`docs/public-demo-corpus.md`](../../docs/public-demo-corpus.md) for the full maintenance contract.
