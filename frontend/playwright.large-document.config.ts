import { defineConfig, devices } from "@playwright/test";

const largeDocumentRoot = process.env.MANGASENSEI_LARGE_DOCUMENT_ROOT;
if (!largeDocumentRoot) {
  throw new Error("MANGASENSEI_LARGE_DOCUMENT_ROOT is required");
}

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
  webServer: {
    command: `uv run python -m tests.large_document.runtime --root "${largeDocumentRoot}"`,
    cwd: "..",
    url: "http://127.0.0.1:8000/ready",
    timeout: 30_000,
    reuseExistingServer: false,
    stdout: "pipe",
    stderr: "pipe",
  },
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
    // Trace snapshots can issue speculative resource requests that are not
    // product browser traffic. Keep E1's request envelope free of harness I/O;
    // HTML reports and failure screenshots remain enabled.
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "large-document-chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
