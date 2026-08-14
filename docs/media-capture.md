# Reproducible media capture

MangaSensei keeps presentation-media generation in source control so screenshots and short browser videos can be regenerated from a known repository revision instead of being manually recorded.

## Evidence classes

The harness keeps three classes separate:

1. **Presentation fixture capture (A)** — executable now. It uses safe synthetic browser/API fixtures to exercise real frontend rendering and interactions. These outputs are presentation artifacts, **not OCR accuracy evidence**.
2. **Full-stack application-flow capture (B)** — the existing [`frontend/playwright.fullstack.config.ts`](../frontend/playwright.fullstack.config.ts) remains the integration proof for browser -> backend -> persistence/worker -> browser. The media foundation does not duplicate that harness or claim its deterministic worker output is real local OCR.
3. **Real-local-OCR publication capture (C)** — requires a reviewed source adapter/runtime path that processes real source bytes through the current application and records reproducible provenance. MangaSensei Public Demo Corpus v1 remains the project-owned canonical presentation/ground-truth dataset. The committed Black Jack fixtures are a separate **third-party authorized real-manga pressure corpus** and may be used for a specifically reviewed publication capture under Sato Manga Works' published secondary-use terms; they are not private MangaSensei data and are not relicensed as project-owned/CC-BY/GPL material.

The current executable source ID remains `synthetic-v1`. Any other `MANGASENSEI_MEDIA_SOURCE` value fails closed until a reviewed adapter is implemented. Rights clearance for a source does not make that adapter exist and does not authorize fabricating or injecting OCR results into a presentation capture.

For Black Jack specifically, the rights review supports GitHub-hosted processing/automated OCR as a reasonable application of the holder's broad secondary-use grant for its official digital data, but the terms contain no cloud-compute-specific clause. That rights conclusion does not weaken runtime security, command-bus allowlists, capability handling, provenance checks, or model-artifact rights restrictions.

## Capture controls

[`frontend/playwright.media.config.ts`](../frontend/playwright.media.config.ts) uses the repository-pinned Playwright/Chromium installation and fixes:

- Chromium only, headless;
- one worker, zero retries, no parallel capture;
- desktop `1440x1000` and mobile `390x844` viewports;
- device scale factor `1`;
- `en-US` browser locale, `UTC` timezone and English UI state;
- light color scheme and reduced motion;
- service workers blocked;
- Playwright trace/screenshot/video auto-recorders disabled so only named media outputs are written;
- CSS animation/transition suppression, hidden caret/focus, fixed capture font stack and `document.fonts.ready` before every screenshot or WebM screencast starts;
- deterministic fixture IDs, polling transitions, filenames and directory layout.

Before either capture format starts, the shared media preflight applies those stabilization controls and scans browser-visible text, DOM attributes and resolved URLs for the deterministic fixture capability strings. The screencast path scans that browser-visible surface again immediately before stopping. Screenshot capture may safely call the same preflight again after a screencast; the controls are intentionally reusable.

Chromium is controlled by the pinned `@playwright/test` version and `playwright install chromium`. Provenance records the actual browser version and the bundled Chromium revision inferred from Playwright's executable path, plus Node/platform/architecture and the computed font stack. For publication-grade pixel identity, run captures in the same reviewed OS/container image as well as the same Playwright revision.

## Commands

Install the exact lockfile and browser revision:

```bash
npm ci
cd frontend
npx --no-install playwright install --with-deps chromium
cd ..
```

Run the lightweight contract/discovery check:

```bash
npm run media:check
```

The contract executes Playwright `--list` itself and fails unless the dedicated media config discovers the canonical stories/projects while the normal `playwright.config.ts` discovers no test under `e2e/media/**`.

Capture all fixture stories:

```bash
npm run media:capture
```

Capture one still smoke/profile:

```bash
npm run media:capture -- --project media-desktop --grep '@media-smoke'
```

Capture the short WebM smoke/profile:

```bash
npm run media:capture -- --project media-desktop --grep '@media-webm-smoke'
```

Override the output root without changing filenames:

```bash
MANGASENSEI_MEDIA_OUTPUT=/tmp/mangasensei-media npm run media:capture
```

Generated output is ignored by Git. The default layout is:

```text
media-output/
  synthetic-v1/
    <scenario>/
      desktop|mobile/
        master.png
        master.webm        # video stories only
        provenance.json
```

## Scenarios

The canonical story inventory is [`frontend/e2e/media/scenarios.json`](../frontend/e2e/media/scenarios.json):

- `reader-desktop` — completed reader, desktop still;
- `reader-mobile` — completed reader, mobile still;
- `core-workflow` — upload -> processing -> completed reader, PNG + short WebM screencast;
- `multipage-partial` — one readable page while a sibling processes;
- `multipage-navigation` — two readable pages and Next navigation, PNG + WebM;
- `study-language-switch` — study English -> Brazilian Portuguese while deterministic dictionary vocabulary remains English and no dictionary-language selector is exposed, PNG + WebM.

These are fixture presentation stories. They must not be described as measured OCR output.

## Real Black Jack publication capture contract

This documentation/provenance change does **not** include a real Black Jack README capture, does not record a new full-page OCR observation, and does not change the root or localized READMEs. That publication work is intentionally deferred to a separate follow-up with a legitimate executable MangaSensei OCR runtime.

A README/product capture using `tests/fixtures/ocr/real_manga/black_jack/**` must be an actual current MangaSensei application run, not a synthetic media story. It must use the exact committed fixture bytes, actual OCR output, and the real reader/study UI, with Gemini disabled. Do not hand-correct OCR, inject fixture text as if it were inference, composite regions, generate replacement imagery, hide a known OCR error, or make a universal accuracy claim from the screenshot.

The preferred desktop source candidate is `v01/black_jack_v01_pdf090.jpg` (source PDF page 90), with page 123 and then page 7 as presentation fallbacks if the current real output on the earlier candidate is materially misleading or broken. If none produces a truthful presentation-quality capture, keep the existing presentation media and record the blocker rather than fabricating a showcase.

For every derived Black Jack screenshot intended for publication, retain enough adjacent or machine-readable provenance to recover:

- source fixture path and SHA-256;
- source PDF page;
- exact MangaSensei application/capture commit;
- capture method/runtime;
- viewport and device scale factor;
- output path, byte count and SHA-256;
- whether Gemini was disabled;
- current observed OCR limitations relevant to the shown page.

The published material must place the copyright holder's required attribution adjacent to the image. For English/non-Japanese public material use `Give My Regards to Black Jack` and `SHUHO SATO`; for Japanese material use `ブラックジャックによろしく` and `佐藤秀峰`. State concisely that the image comes from official Sato Manga Works data under the holder's published secondary-use terms and is not licensed under MangaSensei's GPL.

The existing repository-corpus report checkpoint dated 2026-08-13 does not report a future README/demo in advance. Once a new demo is actually published, re-check the then-current holder terms and perform the required post-publication report within the stated deadline.

## Provenance

Every story writes `provenance.json` without timestamps or credentials. Schema version 1 records:

- MangaSensei repository SHA;
- pinned Playwright package version;
- Chromium version and bundled revision;
- Node version, OS platform and architecture;
- capture class, scenario ID/state and source ID;
- profile, viewport, device scale, locale, timezone, color scheme and reduced-motion setting;
- font readiness/computed font stack;
- every resulting artifact path, byte count and SHA-256.

The capture test re-reads the manifest, re-hashes every PNG/WebM artifact, verifies its byte count and rejects any occurrence of the deterministic fixture capability strings in emitted media/provenance. The shared preflight separately rejects those strings from browser-visible text/DOM/attributes/URLs before capture begins, and screencasts repeat that browser-visible check before stopping. Capability tokens remain fixture-only request data and never enter media URLs or filenames.

## Derivatives with ffmpeg

The browser source masters are PNG and WebM. Derivatives are created separately; no ffmpeg binary is vendored.

The [FFmpeg download page](https://ffmpeg.org/download.html) lists **8.1.2** as the latest stable release as of 2026-08-10. Publication runs should record the exact `ffmpeg -version` line they used. The derivative script prints it before a real conversion.

Examples:

```bash
npm run media:derive -- mp4 media-output/synthetic-v1/core-workflow/desktop/master.webm media-derivatives/core-workflow.mp4
npm run media:derive -- gif media-output/synthetic-v1/core-workflow/desktop/master.webm media-derivatives/core-workflow.gif
npm run media:derive -- still media-output/synthetic-v1/core-workflow/desktop/master.webm media-derivatives/core-workflow.png --at 00:00:02
```

Inspect a command without requiring ffmpeg:

```bash
npm run media:derive -- mp4 input.webm output.mp4 --dry-run
```

The MP4 profile uses H.264 (`libx264`), CRF 18, `yuv420p`, `+faststart`, strips inherited metadata and fixes `creation_time`. GIF generation uses a deterministic `palettegen`/`paletteuse` filter chain at 12 fps. Still extraction selects one frame at an explicit timestamp. Exact derivative bytes can still vary across ffmpeg/libx264 builds, so preserve the WebM master and record the ffmpeg version used for publication.

`gifski` remains optional external tooling; it is not a project dependency.

## CI policy

[`.github/workflows/media-contract.yml`](../.github/workflows/media-contract.yml) runs the catalog/path/derivative/discovery contract plus one representative PNG smoke and one short `core-workflow` WebM smoke. The WebM smoke exercises native Playwright screencast start/stop and the same manifest re-read/re-hash verification used by normal captures. The workflow is path-scoped, includes `package-lock.json` because the locked Playwright/browser graph is capture input, and does not generate the rest of the video suite or commit/upload promotional media. Full capture remains an explicit local/on-demand action.
