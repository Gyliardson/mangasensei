import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-pdf-fullstack",
  outputDir: "../test-results-pdf-fullstack",
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "../playwright-report-pdf-fullstack", open: "never" }],
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
    { name: "pdf-fullstack-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
