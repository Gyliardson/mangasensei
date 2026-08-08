import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e-fullstack",
  outputDir: "../test-results-fullstack",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "../playwright-report-fullstack", open: "never" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "fullstack-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
