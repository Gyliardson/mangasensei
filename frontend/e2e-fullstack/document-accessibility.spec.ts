import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const pagePng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAoklEQVR4nO3PAQ3AIADAMEAS/gUgCxcn2VsF2zx7jz9ZrwO+ZrjOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneG6C9DtAhwwyYwSAAAAAElFTkSuQmCC",
  "base64",
);

async function expectNoAxeViolations(page: import("@playwright/test").Page) {
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
}

test("document reader has no Axe violations on desktop and mobile", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
  await page.getByLabel("Page image").setInputFiles([
    { name: "page-1.png", mimeType: "image/png", buffer: pagePng },
    { name: "page-2.png", mimeType: "image/png", buffer: pagePng },
  ]);
  await page.getByRole("button", { name: "Analyze 2 pages" }).click();
  await expect(page.locator(".document-navigation-shell")).toBeVisible();

  await expectNoAxeViolations(page);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".document-navigation-shell")).toBeVisible();
  await expectNoAxeViolations(page);
});
