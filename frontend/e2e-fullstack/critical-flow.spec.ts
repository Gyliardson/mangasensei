import { expect, test } from "@playwright/test";

// Deterministic 80x120 RGB PNG generated with Pillow; unlike the tiny mocked-
// browser fixture, these bytes must pass MangaSensei's real safe image decoder.
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAo0lEQVR4nO3PsQ3AIADAMOD/T1kZGPtFK6X2Bcm8Z48/WV8HvM1wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdQ9FyQPHnhBvrwAAAABJRU5ErkJggg==",
  "base64",
);

test("completes the real local-first page-analysis lifecycle", async ({ page }) => {
  const statusResponses: Array<Promise<string | null>> = [];
  let protectedImageReads = 0;
  let protectedPageReads = 0;

  page.on("response", (response) => {
    const url = new URL(response.url());
    if (/^\/api\/v1\/pages\/[^/]+\/status$/.test(url.pathname) && response.ok()) {
      statusResponses.push(
        response
          .json()
          .then((payload: { data?: { status?: string } }) => payload.data?.status ?? null)
          .catch(() => null),
      );
    }
    if (/^\/api\/v1\/pages\/[^/]+\/image$/.test(url.pathname) && response.ok()) {
      protectedImageReads += 1;
    }
    if (/^\/api\/v1\/pages\/[^/]+$/.test(url.pathname) && response.ok()) {
      protectedPageReads += 1;
    }
  });

  await page.goto("/");
  await page.getByLabel("Imagem da página").setInputFiles({
    name: "pagina.png",
    mimeType: "image/png",
    buffer: png,
  });
  const uploadResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "POST" && url.pathname === "/api/v1/pages";
  });
  await page.getByRole("button", { name: "Analisar página" }).click();
  const uploadResponse = await uploadResponsePromise;
  expect(uploadResponse.status()).toBe(202);

  await expect(page.getByRole("button", { name: "Região 1: 猫です" })).toBeVisible({
    timeout: 20_000,
  });
  const studyTitle = page.locator("#study-title");
  const rubyTokens = studyTitle.locator("ruby");
  await expect(studyTitle).toBeVisible();
  await expect(rubyTokens).toHaveCount(2);
  await expect(rubyTokens.nth(0)).toContainText("猫");
  await expect(rubyTokens.nth(0).locator("rt")).toHaveText("ネコ");
  await expect(rubyTokens.nth(1)).toContainText("です");
  await expect(rubyTokens.nth(1).locator("rt")).toHaveText("デス");
  await expect(page.getByText("cat")).toBeVisible();
  await expect(page.getByText("JMdict fullstack-fixture · JLPT N5 não oficial")).toBeVisible();
  await expect(page.getByText("Análise contextual indisponível.")).toBeVisible();

  const statuses = (await Promise.all(statusResponses)).filter(
    (status): status is string => status !== null,
  );
  expect(statuses.length).toBeGreaterThanOrEqual(2);
  expect(statuses.some((status) => status !== "completed")).toBe(true);
  expect(statuses.at(-1)).toBe("completed");
  expect(protectedImageReads).toBeGreaterThanOrEqual(1);
  expect(protectedPageReads).toBeGreaterThanOrEqual(1);
});
