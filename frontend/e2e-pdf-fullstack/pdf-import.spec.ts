import { join } from "node:path";

import { expect, test } from "@playwright/test";

test("imports a PDF through the real local render boundary and normal page pipeline", async ({
  page,
}) => {
  const importStatuses: string[] = [];
  page.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    if (response.request().method() === "GET" && /^\/api\/v1\/document-imports\//.test(path)) {
      const payload = (await response.json()) as { data?: { status?: string } };
      if (payload.data?.status) importStatuses.push(payload.data.status);
    }
  });

  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByRole("combobox", { name: "Study language" })).toHaveValue("pt-BR");

  const pdfPath = join(process.cwd(), "tests", "fullstack", "fixtures", "blank-one-page.pdf");
  await page.getByLabel(/Page image/).setInputFiles(pdfPath);
  await expect(page.getByText("1 PDF selected")).toBeVisible();
  await expect(page.getByText(/local rendering happens before OCR/)).toBeVisible();

  const accepted = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/v1/document-imports",
  );
  await page.getByRole("button", { name: "Analyze page" }).click();
  expect((await accepted).status()).toBe(202);

  await expect(page.getByText("Page 1 of 1")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("É um gato.")).toBeVisible({ timeout: 20_000 });
  expect(importStatuses.some((status) => status === "queued" || status === "rendering")).toBe(true);
  expect(importStatuses.at(-1)).toBe("completed");
  expect(page.url()).not.toContain("token");
});