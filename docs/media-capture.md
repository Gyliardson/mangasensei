# Reproducible media capture

MangaSensei keeps presentation-media generation in source control so screenshots and short browser videos can be regenerated from a known repository revision instead of being manually recorded.

## Evidence classes

The harness keeps three classes separate:

1. **Presentation fixture capture (A)** — executable now. It uses safe synthetic browser/API fixtures to exercise real frontend rendering and interactions. These outputs are presentation artifacts, **not OCR accuracy evidence**.
2. **Full-stack application-flow capture (B)** — the existing [`frontend/playwright.fullstack.config.ts`](../frontend/playwright.fullstack.config.ts) remains the integration proof for browser -> backend -> persistence/worker -> browser. The media foundation does not duplicate that harness or claim its deterministic worker output is real local OCR.
3. **Real-local-OCR public-corpus capture (C)** — deferred until the public demo corpus is merged, reviewed and frozen. Add a reviewed source adapter for that corpus; do not repurpose private/licensed manga fixtures as publication defaults.

The current executable source ID is `synthetic-v1`. Any other `MANGASENSEI_MEDIA_SOURCE` value fails closed until a reviewed adapter is implemented.

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
- CSS animation/transition suppression, hidden caret, fixed capture font stack and `document.fonts.ready` before screenshots;
- deterministic fixture IDs, polling transitions, filenames and directory layout.

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

Capture all fixture stories:

```bash
npm run media:capture
```

Capture one story/profile:

```bash
npm run media:capture -- --project media-desktop --grep '@media-smoke'
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
- `dictionary-language-switch` — deterministic English -> German dictionary reprojection, PNG + WebM.

These are fixture presentation stories. They must not be described as measured OCR output.

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

The capture test re-reads the manifest, re-hashes each output and rejects any occurrence of the deterministic fixture capability tokens in emitted media/provenance. Capability tokens remain header-only fixture data and never enter media URLs or filenames.

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

[`.github/workflows/media-contract.yml`](../.github/workflows/media-contract.yml) runs only a lightweight catalog/discovery check plus one representative desktop screenshot/provenance smoke. It does not generate the video suite or commit/upload promotional media. Full capture remains an explicit local/on-demand action.
