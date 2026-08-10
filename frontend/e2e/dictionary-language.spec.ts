import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAACk0lEQVR42u3bsUoDQRAG4M2x7yCCghAhIIKFYuUbpBAbSWlj7bNY26SUNCFF3sBKtBAkIBgQFMXOxt7iYAmbUy8zs7c7t/9WKRKTz9m725nd6Xx/vZucRmEyG9mBrXt1cHjUYufd7U32Efb+E60Z3szNPsLleHy4D/R9u3v77vXnx1tQ29r6Bu7SAAMMMMAAAwxwcist8XF2flHnbdPJtVawJ5w9zet8aqfXDe23gZw1hd7wPuX8gnIrS6U5//WXchG2TZNaKRdhW742KLWSzTEXWrSL7P7xIAI4ipZvLtRpmeZCo5ZjxlpaSXjJQUaEAQY4M/Dw6tJL4iIOwjITEdYTZFoWQYxwdDM5Z6JP6YhmTobIuoajmJn5MLcA4MwNrDeTqHiUZvdrxNk7ve7saZ5WTWuZLSUv/xpzDoct05bs5Qozsy7dPx5ImUMV4p28UmJW3HmYTq6lzA1ttTg/+VCLlBlLy4RHGeS8Isw365vSTLPKa5hj1nrTipM8mNYceVh8WppmD3/isRQ4wu1uBMgxwh30LQEMMBq1DBq10Khl0KiV1Fr69eW5BbbNrW3cpQEGGGCAAc6+jefk9KzO28ajoVawJ6TtlQby2xBO2R4eWbkVpAbt4ZFi2wSpf/Tw8Nk2ZWoItiVro5yLd2yyuVCkXWTXfNQJgKNrmeZCo5ZjLpRqyWYkDwAnO59psxoRBhjgPMDj0TCd/iwvbV5pXY0IA5zyrCbkiatFOCkzLSteeUonYibXACjXcHQzp+JBLPE4c8ML7Gg1LfetDffhxaxaNsZOqy69zJbtw0t058H7ZSJ9eAr2ln77rTVv6Yp3D6NIsJYGGGCAAQYYYICNioNplUerEWE0WyLCBs2WiDDAgccPAXlqNMaGHfoAAAAASUVORK5CYII=",
  "base64",
);

type DictionaryLanguage = "en" | "de" | "pt-BR";

function studyPage(requested: DictionaryLanguage) {
  const german = requested === "de";
  const portuguese = requested === "pt-BR";
  return {
    pageId: "page-dictionary",
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage: "pt-BR",
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: requested,
    fallbackDictionaryLanguage: "en",
    dictionarySources: [
      { ref: "en-source", dataset: "JMdict", productLanguage: "en", sourceVersion: "fixture", normalizedDigestSha256: "a".repeat(64) },
      ...(german ? [{ ref: "de-source", dataset: "JMdict", productLanguage: "de", sourceVersion: "fixture", normalizedDigestSha256: "b".repeat(64) }] : []),
    ],
    expiresAt: "2026-08-11T00:00:00Z",
    imageUrl: "/api/v1/pages/page-dictionary/image",
    dimensions: { width: 80, height: 120 },
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "fixture" },
    error: null,
    regions: [{
      id: "region-001",
      text: "猫犬",
      rawText: "猫犬",
      correctedText: null,
      bbox: { x: 0, y: 0, width: 80, height: 120 },
      normalizedBbox: { x: 0, y: 0, width: 1, height: 1 },
      polygon: null,
      angle: 0,
      confidence: 1,
      readingOrder: 0,
      tokens: [
        { surface: "猫", lemma: "猫", reading: "ネコ", partOfSpeech: "名詞", dictionaryId: "cat" },
        { surface: "犬", lemma: "犬", reading: "イヌ", partOfSpeech: "名詞", dictionaryId: "dog" },
      ],
      translation: "É um gato e um cachorro.",
      explanation: null,
      grammar: [],
      vocabulary: german ? [
        { id: "cat", surface: "猫", lemma: "猫", reading: "ねこ", meanings: ["Katze"], source: "JMdict", effectiveLanguage: "de", fallbackUsed: false, fallbackReason: null, sourceRef: "de-source", jlpt: null },
        { id: "dog", surface: "犬", lemma: "犬", reading: "いぬ", meanings: ["dog"], source: "JMdict", effectiveLanguage: "en", fallbackUsed: true, fallbackReason: "requested_form_not_found", sourceRef: "en-source", jlpt: null },
      ] : [
        { id: "cat", surface: "猫", lemma: "猫", reading: "ねこ", meanings: ["cat"], source: "JMdict", effectiveLanguage: "en", fallbackUsed: portuguese, fallbackReason: portuguese ? "unsupported_requested_language" : null, sourceRef: "en-source", jlpt: null },
        { id: "dog", surface: "犬", lemma: "犬", reading: "いぬ", meanings: ["dog"], source: "JMdict", effectiveLanguage: "en", fallbackUsed: portuguese, fallbackReason: portuguese ? "unsupported_requested_language" : null, sourceRef: "en-source", jlpt: null },
      ],
    }],
  };
}

async function installMockApi(page: import("@playwright/test").Page) {
  let requested: DictionaryLanguage = "en";
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/v1/pages") {
      requested = "en";
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({
        success: true,
        data: {
          pageId: "page-dictionary",
          jobId: "job-upload",
          contentSha256: "a".repeat(64),
          width: 80,
          height: 120,
          mediaType: "image/png",
          expiresAt: "2026-08-11T00:00:00Z",
          studyLanguage: "pt-BR",
          capabilities: { readPage: "read-token", readImage: "image-token", reprocessPage: "reprocess-token" },
        },
        error: null,
      }) });
      return;
    }
    if (request.method() === "POST" && url.pathname.endsWith("/reprocess")) {
      const payload = request.postDataJSON() as { dictionaryLanguage?: DictionaryLanguage; studyLanguage?: string };
      if (payload.dictionaryLanguage) requested = payload.dictionaryLanguage;
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({
        success: true,
        data: { jobId: `job-${requested}`, status: "pending", studyLanguage: "pt-BR", requestedDictionaryLanguage: requested, created: true },
        error: null,
      }) });
      return;
    }
    if (url.pathname.endsWith("/image")) {
      await route.fulfill({ status: 200, contentType: "image/png", body: png });
      return;
    }
    if (url.pathname.endsWith("/status")) {
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ success: true, data: { status: "completed", resultAvailable: true, error: null }, error: null }) });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ success: true, data: studyPage(requested), error: null }) });
  });
}

async function uploadFixture(page: import("@playwright/test").Page) {
  await page.getByLabel("Imagem da página").setInputFiles({ name: "pagina.png", mimeType: "image/png", buffer: png });
  await page.getByRole("button", { name: "Analisar página" }).click();
  await expect(page.getByRole("button", { name: "Região 1: 猫犬" })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mangasensei.ui.locale", "pt-BR"));
  await installMockApi(page);
});

test("operates dictionary preference by keyboard and renders German with per-item English fallback", async ({ page }) => {
  await page.goto("/");
  await uploadFixture(page);

  const dictionary = page.getByRole("combobox", { name: "Idioma do dicionário" });
  await expect(dictionary).toHaveValue("en");
  await dictionary.focus();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");

  await expect(dictionary).toHaveValue("de");
  await expect(page.getByText("Katze", { exact: true })).toHaveAttribute("lang", "de");
  await expect(page.getByText("dog", { exact: true })).toHaveAttribute("lang", "en");
  await expect(page.getByText("Fallback em inglês")).toHaveCount(1);
  await expect(page.getByText("JMdict · Alemão")).toBeVisible();
  await expect(page.getByText("JMdict · Inglês")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
  await expect(page.getByRole("combobox", { name: "Exibição de furigana" })).toHaveValue("hiragana");

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("persists German across re-entry and automatically reprojects a fresh English upload", async ({ page }) => {
  await page.goto("/");
  await uploadFixture(page);
  await page.getByRole("combobox", { name: "Idioma do dicionário" }).selectOption("de");
  await expect(page.getByText("Katze")).toHaveAttribute("lang", "de");

  await page.reload();
  await expect(page.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
  await uploadFixture(page);

  await expect(page.getByRole("combobox", { name: "Idioma do dicionário" })).toHaveValue("de");
  await expect(page.getByText("Katze")).toHaveAttribute("lang", "de");
});

test("presents requested pt-BR as explicit deterministic English fallback", async ({ page }) => {
  await page.goto("/");
  await uploadFixture(page);
  await page.getByRole("combobox", { name: "Idioma do dicionário" }).selectOption("pt-BR");

  await expect(page.getByText("Dicionário solicitado: Português (Brasil)")).toBeVisible();
  await expect(page.getByText(/JMdict determinístico não oferece glosas em português/)).toBeVisible();
  await expect(page.getByText("cat", { exact: true })).toHaveAttribute("lang", "en");
  await expect(page.getByText("dog", { exact: true })).toHaveAttribute("lang", "en");
  await expect(page.getByText("Fallback em inglês")).toHaveCount(2);
  await expect(page.getByRole("combobox", { name: "Idioma da interface" })).toHaveValue("pt-BR");

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
