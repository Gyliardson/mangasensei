import { expect, test } from "@playwright/test";

import {
  captureScreenshot,
  loadScenarioCatalog,
  profileForProject,
  startScreencast,
  stopScreencast,
  writeAndVerifyProvenance,
  type MediaScenario,
} from "./harness";
import {
  installDocumentFixture,
  installSinglePageFixture,
  uploadDocument,
  uploadSinglePage,
} from "./fixtures";

const catalog = await loadScenarioCatalog();
const scenarios = new Map(catalog.scenarios.map((scenario) => [scenario.id, scenario] as const));

function scenario(id: string): MediaScenario {
  const value = scenarios.get(id);
  if (!value) throw new Error(`missing media scenario: ${id}`);
  return value;
}

async function openEnglishUi(page: import("@playwright/test").Page) {
  await page.addInitScript(() => localStorage.setItem("mangasensei.ui.locale", "en"));
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
}

async function finishCapture(
  options: Parameters<typeof writeAndVerifyProvenance>[0],
): Promise<void> {
  const manifestPath = await writeAndVerifyProvenance(options);
  expect(manifestPath.endsWith("provenance.json")).toBe(true);
}

test("reader-desktop @media-smoke", async ({ page, browser }, testInfo) => {
  const item = scenario("reader-desktop");
  test.skip(profileForProject(testInfo.project.name) !== "desktop", "desktop-only capture story");
  await installSinglePageFixture(page);
  await openEnglishUi(page);
  await uploadSinglePage(page);
  await page.getByRole("button", { name: "Region 1: 猫です" }).click();
  await expect(page.getByText("It is a cat.")).toBeVisible();
  const screenshot = await captureScreenshot(page, item.id, "desktop");
  await finishCapture({ browser, page, testInfo, scenario: item, artifacts: [screenshot] });
});

test("reader-mobile", async ({ page, browser }, testInfo) => {
  const item = scenario("reader-mobile");
  test.skip(profileForProject(testInfo.project.name) !== "mobile", "mobile-only capture story");
  await installSinglePageFixture(page);
  await openEnglishUi(page);
  await uploadSinglePage(page);
  await page.getByRole("button", { name: "Region 1: 猫です" }).click();
  await expect(page.getByText("It is a cat.")).toBeVisible();
  const screenshot = await captureScreenshot(page, item.id, "mobile");
  await finishCapture({ browser, page, testInfo, scenario: item, artifacts: [screenshot] });
});

test("core-workflow @media-webm-smoke", async ({ page, browser }, testInfo) => {
  const item = scenario("core-workflow");
  test.skip(profileForProject(testInfo.project.name) !== "desktop", "desktop-only capture story");
  await installSinglePageFixture(page, "workflow");
  await openEnglishUi(page);
  const videoPath = await startScreencast(page, item.id, "desktop");
  await page.waitForTimeout(300);
  await uploadSinglePage(page);
  await page.getByRole("button", { name: "Region 1: 猫です" }).click();
  await expect(page.getByText("It is a cat.")).toBeVisible();
  await page.waitForTimeout(500);
  const video = await stopScreencast(page, videoPath);
  expect(video.path).toBe("synthetic-v1/core-workflow/desktop/master.webm");
  expect(video.bytes).toBeGreaterThan(0);
  expect(video.sha256).toMatch(/^[0-9a-f]{64}$/);
  const screenshot = await captureScreenshot(page, item.id, "desktop");
  await finishCapture({ browser, page, testInfo, scenario: item, artifacts: [screenshot, video] });
});

test("multipage-partial", async ({ page, browser }, testInfo) => {
  const item = scenario("multipage-partial");
  test.skip(profileForProject(testInfo.project.name) !== "desktop", "desktop-only capture story");
  await installDocumentFixture(page, { partial: true });
  await openEnglishUi(page);
  await uploadDocument(page);
  await expect(page.getByText("1 / 2 pages complete · 1 processing · 0 failed")).toBeVisible();
  await expect(page.locator(".document-page-index button").nth(1)).toHaveAttribute(
    "data-page-status",
    "processing",
  );
  const screenshot = await captureScreenshot(page, item.id, "desktop");
  await finishCapture({ browser, page, testInfo, scenario: item, artifacts: [screenshot] });
});

test("multipage-navigation", async ({ page, browser }, testInfo) => {
  const item = scenario("multipage-navigation");
  test.skip(profileForProject(testInfo.project.name) !== "desktop", "desktop-only capture story");
  await installDocumentFixture(page, { partial: false });
  await openEnglishUi(page);
  await uploadDocument(page);
  await expect(page.getByText("2 / 2 pages complete · 0 processing · 0 failed")).toBeVisible();
  const videoPath = await startScreencast(page, item.id, "desktop");
  await page.waitForTimeout(300);
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
  await expect(page.getByRole("button", { name: "Region 1: 雨ですね" })).toBeVisible();
  await page.getByRole("button", { name: "Region 1: 雨ですね" }).click();
  await expect(page.getByText("It is raining, isn't it?")).toBeVisible();
  await page.waitForTimeout(500);
  const video = await stopScreencast(page, videoPath);
  const screenshot = await captureScreenshot(page, item.id, "desktop");
  await finishCapture({ browser, page, testInfo, scenario: item, artifacts: [screenshot, video] });
});

test("dictionary-language-switch", async ({ page, browser }, testInfo) => {
  const item = scenario("dictionary-language-switch");
  test.skip(profileForProject(testInfo.project.name) !== "desktop", "desktop-only capture story");
  await installSinglePageFixture(page);
  await openEnglishUi(page);
  await uploadSinglePage(page);
  await page.getByRole("button", { name: "Region 1: 猫です" }).click();
  const videoPath = await startScreencast(page, item.id, "desktop");
  await page.waitForTimeout(300);
  await page.getByRole("group", { name: "Study preferences" })
    .getByRole("combobox", { name: "Dictionary language" })
    .selectOption("de");
  await expect(page.getByText("Katze", { exact: true })).toHaveAttribute("lang", "de");
  await page.waitForTimeout(500);
  const video = await stopScreencast(page, videoPath);
  const screenshot = await captureScreenshot(page, item.id, "desktop");
  await finishCapture({ browser, page, testInfo, scenario: item, artifacts: [screenshot, video] });
});
