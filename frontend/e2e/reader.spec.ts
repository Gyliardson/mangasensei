import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAACk0lEQVR42u3bsUoDQRAG4M2x7yCCghAhIIKFYuUbpBAbSWlj7bNY26SUNCFF3sBKtBAkIBgQFMXOxt7iYAmbUy8zs7c7t/9WKRKTz9m725nd6Xx/vZucRmEyG9mBrXt1cHjUYufd7U32Efb+E60Z3szNPsLleHy4D/R9u3v77vXnx1tQ29r6Bu7SAAMMMMAAAwxwcist8XF2flHnbdPJtVawJ5w9zet8aqfXDe23gZw1hd7wPuX8gnIrS6U5//WXchG2TZNaKRdhW742KLWSzTEXWrSL7P7xIAI4ipZvLtRpmeZCo5ZjxlpaSXjJQUaEAQY4M/Dw6tJL4iIOwjITEdYTZFoWQYxwdDM5Z6JP6YhmTobIuoajmJn5MLcA4MwNrDeTqHiUZvdrxNk7ve7saZ5WTWuZLSUv/xpzDoct05bs5Qozsy7dPx5ImUMV4p28UmJW3HmYTq6lzA1ttTg/+VCLlBlLy4RHGeS8Isw365vSTLPKa5hj1nrTipM8mNYceVh8WppmD3/isRQ4wu1uBMgxwh30LQEMMBq1DBq10Khl0KiV1Fr69eW5BbbNrW3cpQEGGGCAAc6+jefk9KzO28ajoVawJ6TtlQby2xBO2R4eWbkVpAbt4ZFi2wSpf/Tw8Nk2ZWoItiVro5yLd2yyuVCkXWTXfNQJgKNrmeZCo5ZjLpRqyWYkDwAnO59psxoRBhjgPMDj0TCd/iwvbV5pXY0IA5zyrCbkiatFOCkzLSteeUonYibXACjXcHQzp+JBLPE4c8ML7Gg1LfetDffhxaxaNsZOqy69zJbtw0t058H7ZSJ9eAr2ln77rTVv6Yp3D6NIsJYGGGCAAQYYYICNioNplUerEWE0WyLCBs2WiDDAgccPAXlqNMaGHfoAAAAASUVORK5CYII=",
  "base64",
);

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST" && url.pathname === "/api/v1/pages") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            pageId: "page-001",
            jobId: "job-001",
            contentSha256: "a".repeat(64),
            width: 80,
            height: 120,
            mediaType: "image/png",
            expiresAt: "2026-08-09T00:00:00Z",
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
          data: { status: "completed", error: null },
          error: null,
        }),
      });
      return;
    }
    const localOnly = new URL(page.url()).searchParams.get("mode") === "local";
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        data: {
          pageId: "page-001",
          status: "completed",
          expiresAt: "2026-08-09T00:00:00Z",
          imageUrl: "/api/v1/pages/page-001/image",
          dimensions: { width: 80, height: 120 },
          ocr: { detector: "default", recognizer: "48px", upstreamCommit: "95227a2" },
          error: null,
          regions: [
            {
              id: "region-001",
              text: "猫です",
              rawText: "猫です",
              correctedText: null,
              bbox: { x: 10, y: 20, width: 40, height: 60 },
              normalizedBbox: { x: 0.125, y: 0.1667, width: 0.5, height: 0.5 },
              polygon: [[10, 20], [50, 20], [50, 80], [10, 80]],
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
              translation: localOnly ? null : "É um gato.",
              explanation: localOnly ? null : "Frase nominal polida.",
              grammar: localOnly ? [] : ["です"],
              vocabulary: [
                {
                  id: "jmdict-1467640",
                  surface: "猫",
                  lemma: "猫",
                  reading: "ネコ",
                  meanings: ["gato"],
                  source: "JMdict",
                  jlpt: { level: "N5", official: false },
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

test("uploads a page and opens its study region by keyboard", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByLabel("Imagem da página").setInputFiles({
    name: "pagina.png",
    mimeType: "image/png",
    buffer: png,
  });
  await page.getByRole("button", { name: "Analisar página" }).click();

  const region = page.getByRole("button", { name: "Região 1: 猫です" });
  await expect(region).toBeVisible();
  await region.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("É um gato.")).toBeVisible();
  await expect(page.locator("rt", { hasText: "ねこ" })).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: testInfo.outputPath("reader.png"),
    fullPage: true,
  });
  await page.screenshot({
    path: `../docs/assets/reader-${testInfo.project.name}.png`,
    fullPage: true,
  });
});

test("shows local vocabulary when contextual AI is unavailable", async ({ page }) => {
  await page.goto("/?mode=local");
  await page.getByLabel("Imagem da página").setInputFiles({
    name: "pagina.png",
    mimeType: "image/png",
    buffer: png,
  });
  await page.getByRole("button", { name: "Analisar página" }).click();

  await expect(page.getByText("Análise contextual indisponível.")).toBeVisible();
  await expect(page.getByText("gato")).toBeVisible();
  await expect(page.getByText("JMdict · JLPT N5 não oficial")).toBeVisible();
  await expect(page.getByText("Nenhum ponto gramatical adicional.")).toBeVisible();

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
