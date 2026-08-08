import { expect, test } from "@playwright/test";

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAACk0lEQVR42u3bsUoDQRAG4M2x7yCCghAhIIKFYuUbpBAbSWlj7bNY26SUNCFF3sBKtBAkIBgQFMXOxt7iYAmbUy8zs7c7t/9WKRKTz9m725nd6Xx/vZucRmEyG9mBrXt1cHjUYufd7U32Efb+E60Z3szNPsLleHy4D/R9u3v77vXnx1tQ29r6Bu7SAAMMMMAAAwxwcist8XF2flHnbdPJtVawJ5w9zet8aqfXDe23gZw1hd7wPuX8gnIrS6U5//WXchG2TZNaKRdhW742KLWSzTEXWrSL7P7xIAI4ipZvLtRpmeZCo5ZjxlpaSXjJQUaEAQY4M/Dw6tJL4iIOwjITEdYTZFoWQYxwdDM5Z6JP6YhmTobIuoajmJn5MLcA4MwNrDeTqHiUZvdrxNk7ve7saZ5WTWuZLSUv/xpzDoct05bs5Qozsy7dPx5ImUMV4p28UmJW3HmYTq6lzA1ttTg/+VCLlBlLy4RHGeS8Isw365vSTLPKa5hj1nrTipM8mNYceVh8WppmD3/isRQ4wu1uBMgxwh30LQEMMBq1DBq10Khl0KiV1Fr69eW5BbbNrW3cpQEGGGCAAc6+jefk9KzO28ajoVawJ6TtlQby2xBO2R4eWbkVpAbt4ZFi2wSpf/Tw8Nk2ZWoItiVro5yLd2yyuVCkXWTXfNQJgKNrmeZCo5ZjLpRqyWYkDwAnO59psxoRBhjgPMDj0TCd/iwvbV5pXY0IA5zyrCbkiatFOCkzLSteeUonYibXACjXcHQzp+JBLPE4c8ML7Gg1LfetDffhxaxaNsZOqy69zJbtw0t058H7ZSJ9eAr2ln77rTVv6Yp3D6NIsJYGGGCAAQYYYICNioNplUerEWE0WyLCBs2WiDDAgccPAXlqNMaGHfoAAAAASUVORK5CYII=",
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
  await page.getByRole("button", { name: "Analisar página" }).click();

  await expect(page.getByRole("button", { name: "Região 1: 猫です" })).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByRole("heading", { name: "猫です" })).toBeVisible();
  await expect(page.locator("rt", { hasText: "ネコ" })).toBeVisible();
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
