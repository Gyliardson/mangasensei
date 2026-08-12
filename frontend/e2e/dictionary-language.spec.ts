import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAACk0lEQVR42u3bsUoDQRAG4M2x7yCCghAhIIKFYuUbpBAbSWlj7bNY26SUNCFF3sBKtBAkIBgQFMXOxt7iYAmbUy8zs7c7t/9WKRKTz9m725nd6Xx/vZucRmEyG9mBrXt1cHjUYufd7U32Efb+E60Z3szNPsLleHy4D/R9u3v77vXnx1tQ29r6Bu7SAAMMMMAAAwxwcist8XF2flHnbdPJtVawJ5w9zet8aqfXDe23gZw1hd7wPuX8gnIrS6U5//WXchG2TZNaKRdhW742KLWSzTEXWrSL7P7xIAI4ipZvLtRpmeZCo5ZjxlpaSXjJQUaEAQY4M/Dw6tJL4iIOwjITEdYTZFoWQYxwdDM5Z6JP6YhmTobIuoajmJn5MLcA4MwNrDeTqHiUZvdrxNk7ve7saZ5WTWuZLSUv/xpzDoct05bs5Qozsy7dPx5ImUMV4p28UmJW3HmYTq6lzA1ttTg/+VCLlBlLy4RHGeS8Isw365vSTLPKa5hj1nrTipM8mNYceVh8WppmD3/isRQ4wu1uBMgxwh30LQEMMBq1DBq10Khl0KiV1Fr69eW5BbbNrW3cpQEGGGCAAc6+jefk9KzO28ajoVawJ6TtlQby2xBO2R4eWbkVpAbt4ZFi2wSpf/Tw8Nk2ZWoItiVro5yLd2yyuVCkXWTXfNQJgKNrmeZCo5ZjLpRqyWYkDwAnO59psxoRBhjgPMDj0TCd/iwvbV5pXY0IA5zyrCbkiatFOCkzLSteeUonYibXACjXcHQzp+JBLPE4c8ML7Gg1LfetDffhxaxaNsZOqy69zJbtw0t058H7ZSJ9eAr2ln77rTVv6Yp3D6NIsJYGGGCAAQYYYICNioNplUerEWE0WyLCBs2WiDDAgccPAXlqNMaGHfoAAAAASUVORK5CYII=",
  "base64",
);

type StudyLanguage = "pt-BR" | "en";

function studyPage(studyLanguage: StudyLanguage) {
  const englishStudy = studyLanguage === "en";
  return {
    pageId: "page-dictionary",
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage,
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: "en",
    fallbackDictionaryLanguage: "en",
    dictionarySources: [
      { ref: "en-source", dataset: "JMdict", productLanguage: "en", sourceVersion: "fixture", normalizedDigestSha256: "a".repeat(64) },
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
      translation: englishStudy ? "It is a cat and a dog." : "É um gato e um cachorro.",
      explanation: null,
      grammar: [],
      vocabulary: [
        { id: "cat", surface: "猫", lemma: "猫", reading: "ねこ", meanings: ["cat"], source: "JMdict", effectiveLanguage: "en", fallbackUsed: false, fallbackReason: null, sourceRef: "en-source", jlpt: null },
        { id: "dog", surface: "犬", lemma: "犬", reading: "いぬ", meanings: ["dog"], source: "JMdict", effectiveLanguage: "en", fallbackUsed: false, fallbackReason: null, sourceRef: "en-source", jlpt: null },
      ],
    }],
  };
}

async function installMockApi(page: import("@playwright/test").Page) {
  let studyLanguage: StudyLanguage = "pt-BR";
  const reprocessPayloads: Array<Record<string, string>> = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/v1/pages") {
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
          studyLanguage,
          capabilities: { readPage: "read-token", readImage: "image-token", reprocessPage: "reprocess-token" },
        },
        error: null,
      }) });
      return;
    }
    if (request.method() === "POST" && url.pathname.endsWith("/reprocess")) {
      const payload = request.postDataJSON() as Record<string, string>;
      reprocessPayloads.push(payload);
      if (payload.dictionaryLanguage) {
        await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({
          success: false,
          data: null,
          error: { code: "invalid_request", message: "unsupported dictionary language" },
        }) });
        return;
      }
      if (payload.studyLanguage === "en" || payload.studyLanguage === "pt-BR") {
        studyLanguage = payload.studyLanguage;
      }
      await route.fulfill({ status: 202, contentType: "application/json", body: JSON.stringify({
        success: true,
        data: { jobId: `job-${studyLanguage}`, status: "pending", studyLanguage, created: true },
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
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ success: true, data: studyPage(studyLanguage), error: null }) });
  });
  return reprocessPayloads;
}

async function uploadFixture(page: import("@playwright/test").Page) {
  await page.getByLabel("Imagem da página").setInputFiles({ name: "pagina.png", mimeType: "image/png", buffer: png });
  await page.getByRole("button", { name: "Analisar página" }).click();
  await expect(page.getByRole("button", { name: "Região 1: 猫犬" })).toBeVisible();
}

test("normalizes a stale German preference without exposing or requesting German dictionary data", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("mangasensei.ui.locale", "pt-BR");
    localStorage.setItem("mangasensei.dictionary.language", "de");
  });
  const reprocessPayloads = await installMockApi(page);
  await page.goto("/");
  await uploadFixture(page);

  await expect(page.getByRole("combobox", { name: "Idioma do dicionário" })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
  await expect(page.getByRole("combobox", { name: "Exibição de furigana" })).toHaveValue("hiragana");
  await expect(page.getByText("Dicionário solicitado: Inglês")).toBeVisible();
  await expect(page.getByText("cat", { exact: true })).toHaveAttribute("lang", "en");
  await expect(page.getByText("dog", { exact: true })).toHaveAttribute("lang", "en");
  expect(await page.evaluate(() => localStorage.getItem("mangasensei.dictionary.language"))).toBe("en");
  expect(reprocessPayloads).toEqual([]);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});

test("keeps study-language mutation independent from the English dictionary contract", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mangasensei.ui.locale", "pt-BR"));
  const reprocessPayloads = await installMockApi(page);
  await page.goto("/");
  await uploadFixture(page);

  await page.getByRole("combobox", { name: "Idioma de estudo" }).selectOption("en");

  await expect(page.getByText("It is a cat and a dog.")).toHaveAttribute("lang", "en");
  await expect(page.getByText("cat", { exact: true })).toHaveAttribute("lang", "en");
  await expect(page.getByText("Dicionário solicitado: Inglês")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Idioma do dicionário" })).toHaveCount(0);
  expect(reprocessPayloads).toEqual([{ studyLanguage: "en" }]);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations).toEqual([]);
});
