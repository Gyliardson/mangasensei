import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const portraitPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAo0lEQVR4nO3PsQ3AIADAMOD/O5lZ2PtFK6X2Bcm8Z48/WV8HvM1wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCdQ/56QO7HtBdtAAAAABJRU5ErkJggg==",
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

async function installApiFixture(
  page: Page,
  fixtureName: FixtureName,
  options: { readonly longStudy?: boolean } = {},
) {
  const fixture = fixtures[fixtureName];
  const studyVocabulary = options.longStudy
    ? Array.from({ length: 12 }, (_, index) => ({
      id: `vocabulary-${index}`,
      surface: "猫",
      lemma: "猫",
      reading: "ネコ",
      meanings: [`cat ${index + 1}`],
      source: "fixture",
      jlpt: null,
    }))
    : [];
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
              vocabulary: studyVocabulary,
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
  await expect(page.locator(".page-canvas")).toBeVisible();
}

async function readGeometry(page: Page) {
  return page.evaluate(() => {
    const viewport = document.querySelector<HTMLElement>(".page-viewport");
    const canvas = document.querySelector<HTMLElement>(".page-canvas");
    const image = document.querySelector<HTMLImageElement>(".page-canvas img");
    const overlay = document.querySelector<SVGElement>(".page-canvas svg");
    const study = document.querySelector<HTMLElement>(".study-panel");
    if (!viewport || !canvas || !image || !overlay || !study) {
      throw new Error("reader geometry missing");
    }
    const canvasRect = canvas.getBoundingClientRect();
    const imageRect = image.getBoundingClientRect();
    const overlayRect = overlay.getBoundingClientRect();
    const viewportStyle = getComputedStyle(viewport);
    const studyStyle = getComputedStyle(study);
    return {
      viewportWidth: viewport.clientWidth,
      viewportClientHeight: viewport.clientHeight,
      viewportScrollHeight: viewport.scrollHeight,
      viewportScrollWidth: viewport.scrollWidth,
      viewportOverflowY: viewportStyle.overflowY,
      viewportMaxHeight: viewportStyle.maxHeight,
      viewportTabIndex: viewport.getAttribute("tabindex"),
      canvasWidth: canvasRect.width,
      canvasHeight: canvasRect.height,
      imageWidth: imageRect.width,
      imageHeight: imageRect.height,
      overlayWidth: overlayRect.width,
      overlayHeight: overlayRect.height,
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
      studyOverflowY: studyStyle.overflowY,
      studyMaxHeight: studyStyle.maxHeight,
      studyClientHeight: study.clientHeight,
      studyScrollHeight: study.scrollHeight,
      documentWidth: document.documentElement.scrollWidth,
      windowWidth: window.innerWidth,
    };
  });
}

for (const fixtureName of ["portrait", "landscape"] as const) {
  test(`keeps ${fixtureName} page in document flow and aligned through fit and zoom`, async ({ page }, testInfo) => {
    await installApiFixture(page, fixtureName);
    await page.goto(`/?fixture=${fixtureName}`);
    await enterReader(page, fixtureName);

    const canvas = page.locator(".page-canvas");
    const viewport = page.locator(".page-viewport");
    const fitMode = page.getByRole("combobox", { name: "Ajuste da página" });
    const isMobile = testInfo.project.name === "mobile-chromium";

    await expect(fitMode).toHaveValue(isMobile ? "width" : "comfortable");
    await expect(page.getByLabel("Nível de zoom")).toHaveText("100%");
    await expect(viewport).not.toHaveAttribute("tabindex");

    const initial = await readGeometry(page);
    expect(initial.naturalWidth).toBe(fixtures[fixtureName].width);
    expect(initial.naturalHeight).toBe(fixtures[fixtureName].height);
    expect(Math.abs(initial.imageWidth - initial.overlayWidth)).toBeLessThanOrEqual(1);
    expect(Math.abs(initial.imageHeight - initial.overlayHeight)).toBeLessThanOrEqual(1);
    expect(initial.canvasWidth).toBeLessThanOrEqual(initial.viewportWidth + 1);
    expect(initial.viewportOverflowY).toBe("hidden");
    expect(initial.viewportMaxHeight).toBe("none");
    expect(initial.viewportScrollHeight).toBeLessThanOrEqual(initial.viewportClientHeight + 1);
    expect(initial.studyOverflowY).toBe("visible");
    expect(initial.studyMaxHeight).toBe("none");
    expect(initial.studyScrollHeight).toBeLessThanOrEqual(initial.studyClientHeight + 1);
    expect(initial.documentWidth).toBeLessThanOrEqual(initial.windowWidth + 1);
    if (fixtureName === "portrait" && !isMobile) {
      expect(initial.canvasWidth).toBeLessThan(initial.viewportWidth - 20);
    }

    await fitMode.selectOption("page");
    await expect(canvas).toHaveAttribute("data-fit-mode", "page");
    const fitPageGeometry = await page.evaluate(() => {
      const viewportElement = document.querySelector<HTMLElement>(".page-viewport");
      const canvasElement = document.querySelector<HTMLElement>(".page-canvas");
      if (!viewportElement || !canvasElement) throw new Error("reader viewport geometry missing");
      const rect = viewportElement.getBoundingClientRect();
      const heightBudget = Math.max(240, window.innerHeight - Math.max(rect.top, 0) - 24);
      return {
        viewportWidth: viewportElement.clientWidth,
        canvasWidth: canvasElement.getBoundingClientRect().width,
        canvasHeight: canvasElement.getBoundingClientRect().height,
        heightBudget,
      };
    });
    expect(fitPageGeometry.canvasWidth).toBeLessThanOrEqual(fitPageGeometry.viewportWidth + 1);
    expect(fitPageGeometry.canvasHeight).toBeLessThanOrEqual(fitPageGeometry.heightBudget + 2);

    await fitMode.selectOption("width");
    await expect(canvas).toHaveAttribute("data-fit-mode", "width");
    await expect.poll(() => canvas.evaluate((element) => element.getBoundingClientRect().width)).toBeGreaterThan(0);
    const fitWidthGeometry = await readGeometry(page);
    expect(Math.abs(fitWidthGeometry.canvasWidth - fitWidthGeometry.viewportWidth)).toBeLessThanOrEqual(2);

    await page.getByRole("button", { name: "Aumentar zoom" }).click();
    await expect(page.getByLabel("Nível de zoom")).toHaveText("125%");
    await expect(viewport).toHaveAttribute("data-horizontal-pan", "true");
    await expect(viewport).toHaveAttribute("tabindex", "0");
    const zoomed = await readGeometry(page);
    expect(zoomed.canvasWidth).toBeGreaterThan(zoomed.viewportWidth);
    expect(zoomed.viewportScrollWidth).toBeGreaterThan(zoomed.viewportWidth);
    expect(Math.abs(zoomed.imageWidth - zoomed.overlayWidth)).toBeLessThanOrEqual(1);
    expect(Math.abs(zoomed.imageHeight - zoomed.overlayHeight)).toBeLessThanOrEqual(1);
    expect(zoomed.documentWidth).toBeLessThanOrEqual(zoomed.windowWidth + 1);

    const scrollLeft = await viewport.evaluate((element) => {
      element.scrollLeft = 40;
      return element.scrollLeft;
    });
    expect(scrollLeft).toBeGreaterThan(0);

    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);

    await page.reload();
    await enterReader(page, fixtureName);
    await expect(page.getByRole("combobox", { name: "Ajuste da página" })).toHaveValue("width");
    await expect(page.getByLabel("Nível de zoom")).toHaveText("125%");
  });
}

test("collapses at an intermediate content width before the reader columns become cramped", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop-chromium", "intermediate viewport is covered once");
  await page.setViewportSize({ width: 900, height: 700 });
  await installApiFixture(page, "portrait");
  await page.goto("/?fixture=intermediate");
  await enterReader(page, "portrait");

  const layout = await page.locator(".reader-layout").evaluate((element) => ({
    columns: getComputedStyle(element).gridTemplateColumns.split(" ").filter(Boolean).length,
    width: element.getBoundingClientRect().width,
  }));
  expect(layout.columns).toBe(1);
  expect(layout.width).toBeLessThanOrEqual(900);

  const pageStage = page.locator(".page-stage");
  const studyPanel = page.locator(".study-panel");
  const stageBox = await pageStage.boundingBox();
  const studyBox = await studyPanel.boundingBox();
  expect(stageBox).not.toBeNull();
  expect(studyBox).not.toBeNull();
  expect(studyBox!.y).toBeGreaterThan(stageBox!.y + stageBox!.height - 2);

  expect((await readGeometry(page)).documentWidth).toBeLessThanOrEqual(901);
});

test("keeps page controls sticky only while traversing the manga stage", async ({ page }, testInfo) => {
  if (testInfo.project.name === "desktop-chromium") {
    await page.setViewportSize({ width: 900, height: 700 });
  }
  await installApiFixture(page, "portrait", { longStudy: true });
  await page.goto("/?fixture=sticky");
  await enterReader(page, "portrait");

  const fitMode = page.getByRole("combobox", { name: "Ajuste da página" });
  if (await fitMode.inputValue() !== "width") {
    await fitMode.selectOption("width");
  }
  for (let index = 0; index < 4; index += 1) {
    await page.getByRole("button", { name: "Aumentar zoom" }).click();
  }
  await expect(page.getByLabel("Nível de zoom")).toHaveText("200%");

  const toolbar = page.getByRole("group", { name: "Apresentação da página" });
  const stage = page.locator(".page-stage");
  const study = page.locator(".study-panel");
  const stageTop = await stage.evaluate((element) => element.getBoundingClientRect().top + window.scrollY);
  await page.evaluate((top) => window.scrollTo(0, top + 220), stageTop);
  await expect.poll(() => toolbar.evaluate((element) => element.getBoundingClientRect().top)).toBeLessThanOrEqual(14);
  await expect.poll(() => toolbar.evaluate((element) => element.getBoundingClientRect().top)).toBeGreaterThanOrEqual(7);

  const studyTop = await study.evaluate((element) => element.getBoundingClientRect().top + window.scrollY);
  const beyondStageScroll = studyTop + 80;
  await page.evaluate((top) => window.scrollTo(0, top), beyondStageScroll);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(beyondStageScroll - 1);
  await expect.poll(() => toolbar.evaluate((element) => element.getBoundingClientRect().bottom)).toBeLessThan(8);

  const horizontal = await readGeometry(page);
  expect(horizontal.viewportScrollWidth).toBeGreaterThan(horizontal.viewportWidth);
  expect(horizontal.documentWidth).toBeLessThanOrEqual(horizontal.windowWidth + 1);

  if (testInfo.project.name === "mobile-chromium") {
    await page.evaluate(() => window.scrollTo(0, 0));
    const controlGeometry = await page.evaluate(() => {
      const toolbarElement = document.querySelector<HTMLElement>(".page-presentation-toolbar");
      const fit = document.querySelector<HTMLElement>(".page-fit-preference");
      const zoom = document.querySelector<HTMLElement>(".reader-zoom");
      if (!toolbarElement || !fit || !zoom) throw new Error("mobile controls missing");
      const fitRect = fit.getBoundingClientRect();
      const zoomRect = zoom.getBoundingClientRect();
      return {
        toolbarClientWidth: toolbarElement.clientWidth,
        toolbarScrollWidth: toolbarElement.scrollWidth,
        rowOffset: Math.abs(fitRect.top - zoomRect.top),
      };
    });
    expect(controlGeometry.toolbarScrollWidth).toBeLessThanOrEqual(controlGeometry.toolbarClientWidth + 1);
    expect(controlGeometry.rowOffset).toBeLessThanOrEqual(4);
    await expect(fitMode).toHaveValue("width");
    await expect(fitMode.locator('option[value="comfortable"]')).toHaveCount(0);
  }

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
