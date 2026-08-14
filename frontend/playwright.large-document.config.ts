import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-large-document",
  outputDir: "../test-results-large-document",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: Boolean(process.env.CI),
  timeout: 150_000,
  reporter: [
    ["list"],
    ["html", { outputFolder: "../playwright-report-large-document", open: "never" }],
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
    { name: "large-document-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
