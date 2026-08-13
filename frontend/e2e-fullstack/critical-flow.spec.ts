import { expect, test } from "@playwright/test";

// Deterministic 80x120 RGB PNG generated with Pillow; unlike the tiny mocked-
// browser fixture, these bytes must pass MangaSensei's real safe image decoder.
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAo0lEQVR4nO3PsQ3AIADAMOD/T1kZGPtFK6X2Bcm8Z48/WV8HvM1wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCdQ9FyQPHnhBvrwAAAABJRU5ErkJggg==",
  "base64",
);

interface UploadEnvelope {
  readonly data: {
    readonly studyLanguage: string;
  };
}

interface PersistedStudyPageEnvelope {
  readonly data: {
    readonly contentLanguage: string;
    readonly studyLanguage: string;
    readonly dictionaryLanguage: string;
    readonly requestedDictionaryLanguage?: string;
    readonly dictionarySources?: readonly {
      readonly ref: string;
      readonly dataset: string;
      readonly productLanguage: string;
    }[];
    readonly regions: readonly {
      readonly text: string;
      readonly translation: string | null;
      readonly explanation: string | null;
      readonly grammar: readonly string[];
      readonly vocabulary: readonly {
        readonly meanings: readonly string[];
        readonly effectiveLanguage?: string;
        readonly fallbackUsed?: boolean;
        readonly sourceRef?: string | null;
      }[];
    }[];
  };
}

test("completes real page analysis and study reprojection with English-only dictionary", async ({ page }) => {
  const initialStatusResponses: Array<Promise<string | null>> = [];
  const studyStatusResponses: Array<Promise<string | null>> = [];
  let mutationPhase: "initial" | "study" = "initial";
  let protectedImageReads = 0;
  let protectedPageReads = 0;

  page.on("response", (response) => {
    const url = new URL(response.url());
    if (/^\/api\/v1\/pages\/[^/]+\/status$/.test(url.pathname) && response.ok()) {
      const target = mutationPhase === "study" ? studyStatusResponses : initialStatusResponses;
      target.push(
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
  await expect(page.locator("html")).toHaveAttribute("lang", "pt-BR");
  await expect(page.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
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
  const uploaded = (await uploadResponse.json()) as UploadEnvelope;
  expect(uploaded.data.studyLanguage).toBe("pt-BR");

  const regionButton = page.getByRole("button", { name: "Região 1: 猫です" });
  await expect(regionButton).toBeVisible({ timeout: 20_000 });
  const studyTitle = page.locator("#study-title");
  const rubyTokens = studyTitle.locator("ruby");
  await expect(studyTitle).toBeVisible();
  await expect(studyTitle).toHaveAttribute("lang", "ja");
  await expect(rubyTokens).toHaveCount(1);
  await expect(rubyTokens.nth(0)).toContainText("猫");
  await expect(rubyTokens.nth(0).locator("rt")).toHaveText("ねこ");
  await expect(studyTitle.locator("rt", { hasText: "です" })).toHaveCount(0);
  await expect(studyTitle.locator("rt", { hasText: "デス" })).toHaveCount(0);
  await expect(page.getByText("É um gato.")).toHaveAttribute("lang", "pt-BR");
  await expect(page.getByText("Frase nominal polida.")).toHaveAttribute("lang", "pt-BR");
  await expect(page.getByText("cópula polida", { exact: true })).toBeVisible();
  await expect(page.getByText("cat", { exact: true })).toHaveAttribute("lang", "en");
  await expect(page.getByText("Dicionário solicitado: Inglês")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Idioma do dicionário" })).toHaveCount(0);

  const initialStatuses = (await Promise.all(initialStatusResponses)).filter(
    (status): status is string => status !== null,
  );
  expect(initialStatuses.length).toBeGreaterThanOrEqual(2);
  expect(initialStatuses.some((status) => status !== "completed")).toBe(true);
  expect(initialStatuses.at(-1)).toBe("completed");

  mutationPhase = "study";
  const studyReprocessResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "POST" && /\/api\/v1\/pages\/[^/]+\/reprocess$/.test(url.pathname);
  });
  const englishPersistedResponsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === "GET"
      && /^\/api\/v1\/pages\/[^/]+$/.test(url.pathname)
      && response.ok();
  });
  const studyControls = page.getByRole("group", { name: "Preferências de estudo" });
  await studyControls.getByRole("combobox", { name: "Idioma de estudo" }).selectOption("en");

  const studyReprocessResponse = await studyReprocessResponsePromise;
  expect(studyReprocessResponse.status()).toBe(202);
  expect(studyReprocessResponse.request().postDataJSON()).toEqual({ studyLanguage: "en" });
  expect(await studyReprocessResponse.request().headerValue("x-page-token")).toBeTruthy();

  const englishPersistedResponse = await englishPersistedResponsePromise;
  const persistedEnglish = (await englishPersistedResponse.json()) as PersistedStudyPageEnvelope;
  expect(persistedEnglish.data.contentLanguage).toBe("ja");
  expect(persistedEnglish.data.studyLanguage).toBe("en");
  expect(persistedEnglish.data.dictionaryLanguage).toBe("en");
  expect(persistedEnglish.data.requestedDictionaryLanguage ?? "en").toBe("en");
  expect(persistedEnglish.data.dictionarySources?.every((source) => source.productLanguage === "en")).toBe(true);
  expect(persistedEnglish.data.regions).toHaveLength(1);
  expect(persistedEnglish.data.regions[0]?.text).toBe("猫です");
  expect(persistedEnglish.data.regions[0]?.translation).toBe("It is a cat.");
  expect(persistedEnglish.data.regions[0]?.explanation).toBe("A polite nominal sentence.");
  expect(persistedEnglish.data.regions[0]?.grammar).toEqual(["polite copula"]);
  expect(persistedEnglish.data.regions[0]?.vocabulary[0]?.meanings).toEqual(["cat"]);

  await expect(page.getByText("It is a cat.")).toHaveAttribute("lang", "en");
  await expect(page.getByText("A polite nominal sentence.")).toHaveAttribute("lang", "en");
  await expect(page.getByText("polite copula", { exact: true })).toBeVisible();
  await expect(page.getByText("cat", { exact: true })).toHaveAttribute("lang", "en");
  await expect(regionButton).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "pt-BR");
  await expect(studyControls.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
  await expect(studyControls.getByRole("combobox", { name: "Idioma do dicionário" })).toHaveCount(0);

  const studyStatuses = (await Promise.all(studyStatusResponses)).filter(
    (status): status is string => status !== null,
  );
  expect(studyStatuses.length).toBeGreaterThanOrEqual(1);
  expect(studyStatuses.some((status) => status !== "completed")).toBe(true);
  expect(studyStatuses.at(-1)).toBe("completed");
  expect(protectedImageReads).toBeGreaterThanOrEqual(1);
  expect(protectedPageReads).toBeGreaterThanOrEqual(2);

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "pt-BR");
  await expect(page.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
});
