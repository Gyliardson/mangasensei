import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const portraitPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAo0lEQVR4nO3PsQ3AIADAMOD/O5lZ2PtFK6X2Bcm8Z48/WV8HvM1wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdQ/56QO7HtBdtAAAAABJRU5ErkJggg==",
  "base64",
);
const landscapePng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAHgAAAA8CAIAAAAiz+n/AAAAoklEQVR4nO3QMRHAIADAQMC/zs4s7FXRMPRfQS7z7GfwvXU74C+MjhgdMTpidMToiNERoyNGR4yOGB0xOmJ0xOiI0RGjI0ZHjI4YHTE6YnTE6IjREaMjRkeMjhgdMTpidMToiNERoyNGR4yOGB0xOmJ0xOiI0RGjI0ZHjI4YHTE6YnTE6IjREaMjRkeMjhgdMTpidMToiNERoyNGR4yOGB15AYJZA0OfXOlZAAAAAElFTkSuQmCC",
  "base64",
);

type FixtureName = "portrait" | "landscape";

const fixtures = {
  portrait: { width: 80, height: 120, image: portraitPng },
  landscape: { width: 120, height: 60, image: landscapePng },
} as const;

async function installApiFixture(page: Page, fixtureName: FixtureName) {
  const fixture = fixtures[fixtureName];
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() === "POST" && url.pathname === "/api/v1/pages") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            pageId: `page-${fixtureName}`,
            jobId: `job-${fixtureName}`,
            contentSha256: "a".repeat(64),
            width: fixture.width,
            height: fixture.height,
            mediaType: "image/png",
            expiresAt: "2026-08-10T00:00:00Z",
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
      await route.fulfill({ status: 200, contentType: "image/png", body: fixture.image });
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

    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        data: {
          pageId: `page-${fixtureName}`,
          status: "completed",
          resultAvailable: true,
          expiresAt: "2026-08-10T00:00:00Z",
          imageUrl: `/api/v1/pages/page-${fixtureName}/image`,
          dimensions: { width: fixture.width, height: fixture.height },
          ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "fixture" },
          error: null,
          regions: [
            {
              id: "region-001",
              text: "猫",
              rawText: "猫",
              correctedText: null,
              bbox: {
                x: Math.round(fixture.width * 0.1),
                y: Math.round(fixture.height * 0.1),
                width: Math.round(fixture.width * 0.25),
                height: Math.round(fixture.height * 0.25),
              },
              normalizedBbox: { x: 0.1, y: 0.1, width: 0.25, height: 0.25 },
              polygon: null,
              angle: 0,
              confidence: 0.95,
              readingOrder: 0,
              tokens: [
                {
                  surface: "猫",
                  lemma: "猫",
                  reading: "ネコ",
                  partOfSpeech: "名詞",
                  dictionaryId: null,
                },
              ],
              translation: null,
              explanation: null,
              grammar: [],
              vocabulary: [],
            },
          ],
        },
        error: null,
      }),
    });
  });
}

async function enterReader(page: Page, fixtureName: FixtureName) {
  const fixture = fixtures[fixtureName];
  await page.getByLabel("Imagem da página").setInputFiles({
    name: `${fixtureName}.png`,
    mimeType: "image/png",
    buffer: fixture.image,
  });
  await page.getByRole("button", { name: "Analisar página" }).click();
  await expect(page.getByRole("region", { name: "Visualização da página" })).toBeVisible();
}

for (const fixtureName of ["portrait", "landscape"] as const) {
  test(`keeps ${fixtureName} image and OCR overlay aligned through fit and zoom`, async ({ page }, testInfo) => {
    await installApiFixture(page, fixtureName);
    await page.goto(`/?fixture=${fixtureName}`);
    await enterReader(page, fixtureName);

    const viewport = page.getByRole("region", { name: "Visualização da página" });
    const canvas = page.locator(".page-canvas");
    const fitMode = page.getByRole("combobox", { name: "Ajuste da página" });

    await expect(fitMode).toHaveValue("comfortable");
    await expect(page.getByLabel("Nível de zoom")).toHaveText("100%");
    await expect.poll(() => viewport.evaluate((element) => element.style.maxHeight)).toMatch(/px$/);

    const initial = await page.evaluate(() => {
      const viewportElement = document.querySelector<HTMLElement>(".page-viewport");
      const canvasElement = document.querySelector<HTMLElement>(".page-canvas");
      const imageElement = document.querySelector<HTMLImageElement>(".page-canvas img");
      const overlayElement = document.querySelector<SVGElement>(".page-canvas svg");
      if (!viewportElement || !canvasElement || !imageElement || !overlayElement) {
        throw new Error("reader viewport geometry missing");
      }
      const canvasRect = canvasElement.getBoundingClientRect();
      const imageRect = imageElement.getBoundingClientRect();
      const overlayRect = overlayElement.getBoundingClientRect();
      return {
        viewportWidth: viewportElement.clientWidth,
        canvasWidth: canvasRect.width,
        imageWidth: imageRect.width,
        imageHeight: imageRect.height,
        overlayWidth: overlayRect.width,
        overlayHeight: overlayRect.height,
        naturalWidth: imageElement.naturalWidth,
        naturalHeight: imageElement.naturalHeight,
        documentWidth: document.documentElement.scrollWidth,
        windowWidth: window.innerWidth,
      };
    });

    expect(initial.naturalWidth).toBe(fixtures[fixtureName].width);
    expect(initial.naturalHeight).toBe(fixtures[fixtureName].height);
    expect(Math.abs(initial.imageWidth - initial.overlayWidth)).toBeLessThanOrEqual(1);
    expect(Math.abs(initial.imageHeight - initial.overlayHeight)).toBeLessThanOrEqual(1);
    expect(initial.canvasWidth).toBeLessThanOrEqual(initial.viewportWidth + 1);
    expect(initial.documentWidth).toBeLessThanOrEqual(initial.windowWidth + 1);
    if (fixtureName === "portrait" && testInfo.project.name === "desktop-chromium") {
      expect(initial.canvasWidth).toBeLessThan(initial.viewportWidth - 20);
    }

    await fitMode.selectOption("page");
    await expect(canvas).toHaveAttribute("data-fit-mode", "page");
    const fitPageGeometry = await page.evaluate(() => {
      const viewportElement = document.querySelector<HTMLElement>(".page-viewport");
      const canvasElement = document.querySelector<HTMLElement>(".page-canvas");
      if (!viewportElement || !canvasElement) throw new Error("reader viewport geometry missing");
      return {
        viewportWidth: viewportElement.clientWidth,
        viewportMaxHeight: Number.parseFloat(viewportElement.style.maxHeight),
        canvasWidth: canvasElement.getBoundingClientRect().width,
        canvasHeight: canvasElement.getBoundingClientRect().height,
      };
    });
    expect(fitPageGeometry.canvasWidth).toBeLessThanOrEqual(fitPageGeometry.viewportWidth + 1);
    expect(fitPageGeometry.canvasHeight).toBeLessThanOrEqual(fitPageGeometry.viewportMaxHeight + 1);

    await fitMode.selectOption("width");
    await expect(canvas).toHaveAttribute("data-fit-mode", "width");
    const fitWidthGeometry = await page.evaluate(() => {
      const viewportElement = document.querySelector<HTMLElement>(".page-viewport");
      const canvasElement = document.querySelector<HTMLElement>(".page-canvas");
      if (!viewportElement || !canvasElement) throw new Error("reader viewport geometry missing");
      return {
        viewportWidth: viewportElement.clientWidth,
        canvasWidth: canvasElement.getBoundingClientRect().width,
      };
    });
    expect(Math.abs(fitWidthGeometry.canvasWidth - fitWidthGeometry.viewportWidth)).toBeLessThanOrEqual(2);

    await page.getByRole("button", { name: "Aumentar zoom" }).click();
    await expect(page.getByLabel("Nível de zoom")).toHaveText("125%");
    const zoomed = await page.evaluate(() => {
      const viewportElement = document.querySelector<HTMLElement>(".page-viewport");
      const canvasElement = document.querySelector<HTMLElement>(".page-canvas");
      const imageElement = document.querySelector<HTMLImageElement>(".page-canvas img");
      const overlayElement = document.querySelector<SVGElement>(".page-canvas svg");
      if (!viewportElement || !canvasElement || !imageElement || !overlayElement) {
        throw new Error("reader viewport geometry missing");
      }
      const imageRect = imageElement.getBoundingClientRect();
      const overlayRect = overlayElement.getBoundingClientRect();
      return {
        viewportWidth: viewportElement.clientWidth,
        canvasWidth: canvasElement.getBoundingClientRect().width,
        imageWidth: imageRect.width,
        imageHeight: imageRect.height,
        overlayWidth: overlayRect.width,
        overlayHeight: overlayRect.height,
        documentWidth: document.documentElement.scrollWidth,
        windowWidth: window.innerWidth,
      };
    });
    expect(zoomed.canvasWidth).toBeGreaterThan(zoomed.viewportWidth);
    expect(Math.abs(zoomed.imageWidth - zoomed.overlayWidth)).toBeLessThanOrEqual(1);
    expect(Math.abs(zoomed.imageHeight - zoomed.overlayHeight)).toBeLessThanOrEqual(1);
    expect(zoomed.documentWidth).toBeLessThanOrEqual(zoomed.windowWidth + 1);

    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);

    await page.reload();
    await enterReader(page, fixtureName);
    await expect(page.getByRole("combobox", { name: "Ajuste da página" })).toHaveValue("width");
    await expect(page.getByLabel("Nível de zoom")).toHaveText("125%");
  });
}
