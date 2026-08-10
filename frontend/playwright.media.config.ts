import { defineConfig } from "@playwright/test";

import { MEDIA_PROFILES } from "./e2e/media/harness";

export default defineConfig({
  testDir: "./e2e/media",
  outputDir: "../test-results-media",
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 60_000,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    headless: true,
    locale: "en-US",
    timezoneId: "UTC",
    colorScheme: "light",
    reducedMotion: "reduce",
    deviceScaleFactor: 1,
    serviceWorkers: "block",
    trace: "off",
    screenshot: "off",
    video: "off",
    storageState: {
      cookies: [],
      origins: [
        {
          origin: "http://127.0.0.1:4173",
          localStorage: [{ name: "mangasensei.ui.locale", value: "en" }],
        },
      ],
    },
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [
    {
      name: MEDIA_PROFILES.desktop.projectName,
      use: {
        viewport: MEDIA_PROFILES.desktop.viewport,
        deviceScaleFactor: MEDIA_PROFILES.desktop.deviceScaleFactor,
        isMobile: MEDIA_PROFILES.desktop.isMobile,
        hasTouch: MEDIA_PROFILES.desktop.hasTouch,
      },
    },
    {
      name: MEDIA_PROFILES.mobile.projectName,
      use: {
        viewport: MEDIA_PROFILES.mobile.viewport,
        deviceScaleFactor: MEDIA_PROFILES.mobile.deviceScaleFactor,
        isMobile: MEDIA_PROFILES.mobile.isMobile,
        hasTouch: MEDIA_PROFILES.mobile.hasTouch,
      },
    },
  ],
});
