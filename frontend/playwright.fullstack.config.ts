import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-fullstack",
  outputDir: "../test-results-fullstack",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  // The real-server harness shares one database, rate limiter, and stateful deterministic worker.
  // Retrying a test in the same worker would reuse fixture attempt history instead of reproducing it.
  retries: 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "../playwright-report-fullstack", open: "never" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8000",
    storageState: {
      cookies: [],
      origins: [
        {
          origin: "http://127.0.0.1:8000",
          localStorage: [{ name: "mangasensei.ui.locale", value: "pt-BR" }],
        },
      ],
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "fullstack-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});