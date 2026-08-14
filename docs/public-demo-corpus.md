# Public demo corpus contract

MangaSensei Public Demo Corpus v1 is the project's canonical owned presentation and ground-truth dataset. Its source artwork, rendered pages and annotations live under [`assets/public-demo/`](../assets/public-demo/).

## Rights boundary

The public demo corpus is licensed under CC BY 4.0. MangaSensei source code remains GPL-3.0-only. The **third-party authorized** *Give My Regards to Black Jack* regression fixtures remain a separate professional real-manga evidence source redistributed under Sato Manga Works' published secondary-use terms; they are not part of, and are not relicensed by, the public demo corpus.

The [current official Sato Manga Works secondary-use terms](https://densho810.com/free/) permit commercial and non-commercial reproduction/public transmission and secondary use subject to their conditions, including required title/author attribution and post-use reporting. That separate permission is not needed for these four project-owned pages and does not make the Black Jack material project-owned, CC BY, public domain or part of MangaSensei's GPL license.

MangaSensei Public Demo Corpus v1 remains the canonical deterministic annotation/ground-truth corpus. Authorized Black Jack pages are a real-world OCR pressure/validation corpus and may support separately reviewed product demonstrations; they do not replace this ground-truth dataset.

Each manifest page may list `intendedUseCases` for MangaSensei project planning. This field is non-normative metadata, is not a permissions whitelist, and does not narrow or otherwise restrict the CC BY 4.0 license grant.

## Page contract

Every canonical page is exactly 1440×2048 RGB PNG and has a project-authored SVG source with the same page ID. The corpus intentionally includes easy and difficult content rather than optimizing only for OCR-friendly samples.

- P1 station: sparse presentation page, vertical dialogue, horizontal signage and furigana.
- P2 library: dense four-panel `upper-right → upper-left → lower-right → lower-left` manga flow.
- P3 laboratory: `博士`/`土曜日`, `ロボット`/`入口`, furigana, detached bōten and explicit graphical negative controls.
- P4 rain/shopping street: diagonal SFX, thought text, signage, numerals and texture/line-art negative controls.

## Ground-truth rules

Ground truth is derived from source intent, not MangaSensei OCR output. `transcription.raw` preserves the authored string and must already be Unicode NFC under the declared `strict-nfc-v1` contract. Validation fails on non-NFC text; validation and freezing do not silently normalize or rewrite ground truth. No NFKC, kana folding, punctuation folding, width folding or spelling correction is applied. Furigana is a separate region related to a base text span. Bōten is presentation metadata and never mutates lexical transcription. Environmental text and SFX remain positive text when they are intentionally written language.

The v1 text-role enum is `dialogue`, `thought`, `narration`, `environmental`, `sfx`, `uncertain`. This aligns with current #101 research direction without making production role classification a prerequisite.

Annotations record review metadata explicitly. v1 does not claim a native-human language review or adjudication that did not occur; `ocrConsultedDuringAuthoring` must remain false.

## Fonts

The renderer uses Noto Sans CJK JP and Noto Serif CJK JP, both under SIL OFL 1.1, from the official `notofonts/noto-cjk` repository. Font binaries are runtime build inputs and are not committed.

Pinned upstream resources:

- Noto Sans CJK `Sans2.004`: `NotoSansCJK-Regular.ttc` and `NotoSansCJK-Bold.ttc`.
- Noto Serif CJK `Serif2.003`: `NotoSerifCJK-Regular.ttc` and `NotoSerifCJK-Bold.ttc`.

Exact Git blob IDs, byte sizes and SHA-256 values are in [`assets/public-demo/provenance/fonts.json`](../assets/public-demo/provenance/fonts.json). `scripts/public_demo/fetch_fonts.py` verifies every font before use and refuses a missing or mismatched file. The renderer uses private `MangaSensei ... v1` font aliases so a host font with the same public family name cannot silently satisfy the contract.

## Rendering

Canonical rendering uses the Chromium revision resolved by the exact locked `@playwright/test` 1.62.1 package. Install Node dependencies from `package-lock.json`, install Playwright Chromium, acquire the verified fonts, then run:

```bash
python scripts/public_demo/fetch_fonts.py
node scripts/public_demo/render.mjs
python scripts/public_demo/freeze.py
python scripts/public_demo/validate.py
```

`freeze.py` normalizes screenshots to single-frame RGB PNG with the project's pinned Pillow 12.3.0, writes the rendered Chromium version, updates annotation image hashes and freezes the complete manifest inventory. It does not normalize annotation transcriptions.

Render determinism means two runs from the same frozen SVG/font/Playwright/Pillow inputs must produce byte-identical canonical PNGs after normalization. Any toolchain change that alters bytes requires an explicit corpus revision and visual review rather than silently updating hashes.

## Integrity

[`scripts/public_demo/validate.py`](../scripts/public_demo/validate.py) checks:

- exact manifest inventory and SHA-256;
- four unique page IDs;
- source/image/annotation hashes;
- PNG format, RGB mode and 1440×2048 dimensions;
- annotation image hashes;
- every `transcription.raw` is already NFC-normalized under `strict-nfc-v1`;
- unique region/negative-zone IDs;
- in-bounds polygons and bboxes;
- exact reading-order references;
- furigana base/ruby relationships;
- presentation-mark references and lexical non-mutation;
- negative-zone source markers;
- font provenance completeness;
- frozen rendering toolchain metadata.

No benchmark evaluator is implemented in this increment.

## Editing and review workflow

1. Edit SVG/text from source intent.
2. Update annotation intent without consulting OCR output.
3. Review Japanese strings and all geometry/reference contracts.
4. Render twice from the pinned toolchain and compare PNG SHA-256 values.
5. Visually inspect all four renders.
6. Run `freeze.py` once the source and annotation intent are fixed.
7. Run contract tests and Markdown-link validation.
8. Only after annotation freeze may a separate characterization OCR run be executed; it must never feed corrections back into ground truth.

Future media tooling should consume page/image IDs from the manifest and must not own or rewrite annotation semantics.
