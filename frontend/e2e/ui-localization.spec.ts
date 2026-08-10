import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAACk0lEQVR42u3bsUoDQRAG4M2x7yCCghAhIIKFYuUbpBAbSWlj7bNY26SUNCFF3sBKtBAkIBgQFMXOxt7iYAmbUy8zs7c7t/9WKRKTz9m725nd6Xx/vZucRmEyG9mBrXt1cHjUYufd7U32Efb+E60Z3szNPsLleHy4D/R9u3v77vXnx1tQ29r6Bu7SAAMMMMAAAwxwcist8XF2flHnbdPJtVawJ5w9zet8aqfXDe23gZw1hd7wPuX8gnIrS6U5//WXchG2TZNaKRdhW742KLWSzTEXWrSL7P7xIAI4ipZvLtRpmeZCo5ZjxlpaSXjJQUaEAQY4M/Dw6tJL4iIOwjITEdYTZFoWQYxwdDM5Z6JP6YhmTobIuoajmJn5MLcA4MwNrDeTqHiUZvdrxNk7ve7saZ5WTWuZLSUv/xpzDoct05bs5Qozsy7dPx5ImUMV4p28UmJW3HmYTq6lzA1ttTg/+VCLlBlLy4RHGeS8Isw365vSTLPKa5hj1nrTipM8mNYceVh8WppmD3/isRQ4wu1uBMgxwh30LQEMMBq1DBq10Khl0KiV1Fr69eW5BbbNrW3cpQEGGGCAAc6+jefk9KzO28ajoVawJ6TtlQby2xBO2R4eWbkVpAbt4ZFi2wSpf/Tw8Nk2ZWoItiVro5yLd2yyuVCkXWTXfNQJgKNrmeZCo5ZjLpRqyWYkDwAnO59psxoRBhjgPMDj0TCd/iwvbV5pXY0IA5zyrCbkiatFOCkzLSteeUonYibXACjXcHQzp+JBLPE4c8ML7Gg1LfetDffhxaxaNsZOqy69zJbtw0t058H7ZSJ9eAr2ln77rTVv6Yp3D6NIsJYGGGCAAQYYYICNioNplUerEWE0WyLCBs2WiDDAgccPAXlqNMaGHfoAAAAASUVORK5CYII=",
  "base64",
);

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.removeItem("mangasensei.ui.locale");
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/v1/pages") {
      const englishStudy = (request.postData() ?? "").includes("\r\n\r\nen\r\n");
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            pageId: "page-localization",
            jobId: "job-localization",
            contentSha256: "a".repeat(64),
            width: 80,
            height: 120,
            mediaType: "image/png",
            expiresAt: "2026-08-10T18:00:00Z",
            studyLanguage: englishStudy ? "en" : "pt-BR",
            capabilities: {
              readPage: "read-page-token",
              readImage: "read-image-token",
              reprocessPage: "reprocess-token",
            },
          },
          error: null,
        }),
      });
      return;
    }
    if (url.pathname.endsWith("/image")) {
      await route.fulfill({ status: 200, contentType: "image/png", body: png });
      return;
    }
    if (url.pathname.endsWith("/status")) {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: { status: "completed", resultAvailable: true, error: null },
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        data: {
          pageId: "page-localization",
          status: "completed",
          resultAvailable: true,
          contentLanguage: "ja",
          studyLanguage: "en",
          dictionaryLanguage: "en",
          expiresAt: "2026-08-10T18:00:00Z",
          imageUrl: "/api/v1/pages/page-localization/image",
          dimensions: { width: 80, height: 120 },
          ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "fixture" },
          error: null,
          regions: [
            {
              id: "region-localization",
              text: "猫です",
              rawText: "猫です",
              correctedText: null,
              bbox: { x: 10, y: 20, width: 40, height: 60 },
              normalizedBbox: { x: 0.125, y: 0.1667, width: 0.5, height: 0.5 },
              polygon: null,
              angle: 0,
              confidence: 0.97,
              readingOrder: 0,
              tokens: [
                {
                  surface: "猫",
                  lemma: "猫",
                  reading: "ネコ",
                  partOfSpeech: "名詞",
                  dictionaryId: "jmdict-1467640",
                },
              ],
              translation: "It is a cat.",
              explanation: "A polite nominal sentence.",
              grammar: ["polite copula"],
              vocabulary: [
                {
                  id: "jmdict-1467640",
                  surface: "猫",
                  lemma: "猫",
                  reading: "ネコ",
                  meanings: ["cat"],
                  source: "JMdict",
                  jlpt: null,
                },
              ],
            },
          ],
        },
        error: null,
      }),
    });
  });
});

test("persists UI locale independently through a representative reader flow", async ({ page }) => {
  await page.goto("/");

  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "Read Japanese in context" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Interface language" })).toHaveValue("en");
  await expect(page.getByRole("combobox", { name: "Study language" })).toHaveValue("pt-BR");

  await page.getByRole("combobox", { name: "Study language" }).selectOption("en");
  await page.getByRole("combobox", { name: "Interface language" }).selectOption("pt-BR");
  await expect(page.locator("html")).toHaveAttribute("lang", "pt-BR");
  await expect(page.getByRole("heading", { name: "Leia japonês no contexto" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "pt-BR");
  await expect(page.getByRole("combobox", { name: "Idioma da interface" })).toHaveValue("pt-BR");
  await expect(page.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");

  await page.getByLabel("Imagem da página").setInputFiles({
    name: "pagina.png",
    mimeType: "image/png",
    buffer: png,
  });
  await page.getByRole("button", { name: "Analisar página" }).click();
  await expect(page.getByRole("button", { name: "Região 1: 猫です" })).toBeVisible();
  await expect(page.getByText("It is a cat.")).toHaveAttribute("lang", "en");
  await expect(page.getByText("cat", { exact: true })).toHaveAttribute("lang", "en");
  await expect(page.locator("#study-title")).toHaveAttribute("lang", "ja");

  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("heading", { name: "Select a region" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Study language" })).toHaveValue("en");
  await expect(page.getByText("local meanings in English")).toBeVisible();
  await expect(page.locator("#study-title")).toHaveAttribute("lang", "ja");

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
