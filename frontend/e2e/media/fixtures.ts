import { expect, type Page } from "@playwright/test";

import { MEDIA_FIXTURE_SECRETS } from "./harness";

const uploadPng = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z5VQAAAAASUVORK5CYII=",
  "base64",
);

const pageOneSvg = `
<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900">
  <rect width="600" height="900" fill="#fff"/>
  <g fill="none" stroke="#111" stroke-width="7">
    <rect x="25" y="25" width="550" height="245"/>
    <rect x="25" y="290" width="260" height="290"/>
    <rect x="315" y="290" width="260" height="290"/>
    <rect x="25" y="600" width="550" height="275"/>
    <rect x="70" y="70" width="230" height="45" stroke-width="5"/>
    <line x1="70" y1="160" x2="520" y2="160" stroke-width="5"/>
    <line x1="70" y1="195" x2="520" y2="195" stroke-width="5"/>
    <ellipse cx="150" cy="405" rx="60" ry="60" stroke-width="6"/>
    <path d="M150 465 L115 550 M150 465 L205 550" stroke-width="6"/>
    <ellipse cx="442" cy="395" rx="92" ry="60" stroke-width="5"/>
    <path d="M405 445 L390 500 L440 458" stroke-width="5"/>
    <ellipse cx="435" cy="100" rx="95" ry="45" stroke-width="5"/>
    <path d="M410 135 L380 185 L440 143" stroke-width="5"/>
    <path d="M70 835 V715 H145 V835 M170 835 V655 H245 V835 M290 835 V700 H365 V835 M410 835 V635 H485 V835" stroke-width="4"/>
  </g>
  <g fill="#111">
    <circle cx="120" cy="398" r="7"/><circle cx="180" cy="398" r="7"/>
    <path d="M125 435 Q150 450 175 435" fill="none" stroke="#111" stroke-width="5"/>
  </g>
</svg>`;

const pageTwoSvg = `
<svg xmlns="http://www.w3.org/2000/svg" width="600" height="900" viewBox="0 0 600 900">
  <rect width="600" height="900" fill="#fff"/>
  <g fill="none" stroke="#111" stroke-width="7">
    <rect x="25" y="25" width="255" height="395"/>
    <rect x="310" y="25" width="265" height="395"/>
    <rect x="25" y="450" width="550" height="425"/>
    <ellipse cx="150" cy="130" rx="82" ry="60" stroke-width="5"/>
    <path d="M180 180 L220 235 L155 195" stroke-width="5"/>
    <ellipse cx="440" cy="140" rx="90" ry="70" stroke-width="5"/>
    <path d="M390 198 L355 260 L420 205" stroke-width="5"/>
    <ellipse cx="180" cy="535" rx="90" ry="55" stroke-width="5"/>
    <path d="M210 575 L250 625 L195 585" stroke-width="5"/>
    <path d="M160 790 L300 650 L440 790 M300 650 V835" stroke-width="5"/>
    <path d="M250 785 Q300 735 350 785" stroke-width="5"/>
  </g>
  <g stroke="#111" stroke-width="3">
    <path d="M65 500 L40 565 M120 500 L95 565 M175 500 L150 565 M230 500 L205 565 M285 500 L260 565 M340 500 L315 565 M395 500 L370 565 M450 500 L425 565 M505 500 L480 565"/>
    <path d="M80 610 L55 675 M150 610 L125 675 M220 610 L195 675 M380 610 L355 675 M450 610 L425 675 M520 610 L495 675"/>
  </g>
</svg>`;

const [readPageToken, readImageToken, reprocessToken, readDocumentToken, readDocumentImageToken, reprocessDocumentToken] = MEDIA_FIXTURE_SECRETS;

type DictionaryLanguage = "en" | "de";
type SingleFixtureMode = "completed" | "workflow";

function studyPage(options: {
  readonly pageId: string;
  readonly imageUrl: string;
  readonly dictionaryLanguage?: DictionaryLanguage;
  readonly second?: boolean;
}) {
  const german = options.dictionaryLanguage === "de";
  return {
    pageId: options.pageId,
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage: "en",
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: options.dictionaryLanguage ?? "en",
    fallbackDictionaryLanguage: "en",
    dictionarySources: [
      { ref: "en-fixture", dataset: "JMdict", productLanguage: "en", sourceVersion: "fixture", normalizedDigestSha256: "a".repeat(64) },
      ...(german ? [{ ref: "de-fixture", dataset: "JMdict", productLanguage: "de", sourceVersion: "fixture", normalizedDigestSha256: "b".repeat(64) }] : []),
    ],
    expiresAt: "2030-01-01T00:00:00Z",
    imageUrl: options.imageUrl,
    dimensions: { width: 600, height: 900 },
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "fixture" },
    error: null,
    regions: [
      {
        id: `${options.pageId}-region-1`,
        text: options.second ? "雨ですね" : "猫です",
        rawText: options.second ? "雨ですね" : "猫です",
        correctedText: null,
        bbox: options.second
          ? { x: 100, y: 480, width: 180, height: 120 }
          : { x: 340, y: 335, width: 190, height: 120 },
        normalizedBbox: options.second
          ? { x: 0.1667, y: 0.5333, width: 0.3, height: 0.1333 }
          : { x: 0.5667, y: 0.3722, width: 0.3167, height: 0.1333 },
        polygon: null,
        angle: 0,
        confidence: 0.98,
        readingOrder: 0,
        tokens: [
          {
            surface: options.second ? "雨" : "猫",
            lemma: options.second ? "雨" : "猫",
            reading: options.second ? "アメ" : "ネコ",
            partOfSpeech: "名詞",
            dictionaryId: options.second ? "rain" : "cat",
          },
        ],
        translation: options.second ? "It is raining, isn't it?" : "It is a cat.",
        explanation: options.second ? "A conversational observation." : "A polite nominal sentence.",
        grammar: [options.second ? "sentence-ending particle ね" : "polite copula"],
        vocabulary: [
          {
            id: options.second ? "rain" : "cat",
            surface: options.second ? "雨" : "猫",
            lemma: options.second ? "雨" : "猫",
            reading: options.second ? "あめ" : "ねこ",
            meanings: [german ? (options.second ? "Regen" : "Katze") : (options.second ? "rain" : "cat")],
            source: "JMdict",
            effectiveLanguage: german ? "de" : "en",
            fallbackUsed: false,
            fallbackReason: null,
            sourceRef: german ? "de-fixture" : "en-fixture",
            jlpt: { level: "N5", official: false },
          },
        ],
      },
    ],
  };
}

export async function installSinglePageFixture(page: Page, mode: SingleFixtureMode = "completed") {
  let dictionaryLanguage: DictionaryLanguage = "en";
  let statusReads = 0;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/v1/pages") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            pageId: "media-page-1",
            jobId: "media-job-1",
            contentSha256: "c".repeat(64),
            width: 600,
            height: 900,
            mediaType: "image/png",
            expiresAt: "2030-01-01T00:00:00Z",
            studyLanguage: "en",
            capabilities: {
              readPage: readPageToken,
              readImage: readImageToken,
              reprocessPage: reprocessToken,
            },
          },
          error: null,
        }),
      });
      return;
    }
    if (request.method() === "POST" && url.pathname.endsWith("/reprocess")) {
      const payload = request.postDataJSON() as { dictionaryLanguage?: DictionaryLanguage };
      if (payload.dictionaryLanguage) dictionaryLanguage = payload.dictionaryLanguage;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            jobId: "media-job-reprocess",
            status: "pending",
            studyLanguage: "en",
            requestedDictionaryLanguage: dictionaryLanguage,
            created: true,
          },
          error: null,
        }),
      });
      return;
    }
    if (url.pathname.endsWith("/image")) {
      await route.fulfill({ status: 200, contentType: "image/svg+xml", body: pageOneSvg });
      return;
    }
    if (url.pathname.endsWith("/status")) {
      statusReads += 1;
      const complete = mode === "completed" || statusReads >= 2;
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            status: complete ? "completed" : "processing_ocr",
            resultAvailable: complete,
            error: null,
          },
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        data: studyPage({
          pageId: "media-page-1",
          imageUrl: "/api/v1/pages/media-page-1/image",
          dictionaryLanguage,
        }),
        error: null,
      }),
    });
  });
}

interface DocumentFixtureOptions {
  readonly partial: boolean;
}

function documentPages(partial: boolean) {
  return [
    { pageId: "media-page-a", ordinal: 0, status: "completed", resultAvailable: true },
    {
      pageId: "media-page-b",
      ordinal: 1,
      status: partial ? "processing_ocr" : "completed",
      resultAvailable: !partial,
    },
  ];
}

function documentProgress(partial: boolean) {
  return {
    totalPages: 2,
    completedPages: partial ? 1 : 2,
    processingPages: partial ? 1 : 0,
    failedPages: 0,
  };
}

export async function installDocumentFixture(page: Page, options: DocumentFixtureOptions) {
  const dictionaryByPage = new Map<string, DictionaryLanguage>([
    ["media-page-a", "en"],
    ["media-page-b", "en"],
  ]);
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/v1/documents") {
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            documentId: "media-document-1",
            sourceKind: "images",
            expiresAt: "2030-01-01T00:00:00Z",
            orderRevision: 1,
            pages: documentPages(options.partial),
            progress: documentProgress(options.partial),
            capabilities: {
              readDocument: readDocumentToken,
              readDocumentImage: readDocumentImageToken,
              reprocessDocument: reprocessDocumentToken,
            },
          },
          error: null,
        }),
      });
      return;
    }
    if (request.method() === "GET" && url.pathname === "/api/v1/documents/media-document-1") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            documentId: "media-document-1",
            sourceKind: "images",
            expiresAt: "2030-01-01T00:00:00Z",
            orderRevision: 1,
            pages: documentPages(options.partial),
            progress: documentProgress(options.partial),
          },
          error: null,
        }),
      });
      return;
    }
    const pageMatch = url.pathname.match(/^\/api\/v1\/documents\/media-document-1\/pages\/(media-page-[ab])$/);
    if (request.method() === "GET" && pageMatch?.[1]) {
      const pageId = pageMatch[1];
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: studyPage({
            pageId,
            imageUrl: `/api/v1/documents/media-document-1/pages/${pageId}/image`,
            dictionaryLanguage: dictionaryByPage.get(pageId),
            second: pageId === "media-page-b",
          }),
          error: null,
        }),
      });
      return;
    }
    const imageMatch = url.pathname.match(/^\/api\/v1\/documents\/media-document-1\/pages\/(media-page-[ab])\/image$/);
    if (request.method() === "GET" && imageMatch?.[1]) {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: imageMatch[1] === "media-page-a" ? pageOneSvg : pageTwoSvg,
      });
      return;
    }
    const reprocessMatch = url.pathname.match(/^\/api\/v1\/documents\/media-document-1\/pages\/(media-page-[ab])\/reprocess$/);
    if (request.method() === "POST" && reprocessMatch?.[1]) {
      const payload = request.postDataJSON() as { dictionaryLanguage?: DictionaryLanguage };
      if (payload.dictionaryLanguage) dictionaryByPage.set(reprocessMatch[1], payload.dictionaryLanguage);
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          success: true,
          data: {
            jobId: "media-document-reprocess",
            status: "pending",
            studyLanguage: "en",
            requestedDictionaryLanguage: payload.dictionaryLanguage ?? "en",
            created: true,
          },
          error: null,
        }),
      });
      return;
    }
    await route.fulfill({ status: 404, body: "not found" });
  });
}

export async function uploadSinglePage(page: Page): Promise<void> {
  await page.getByLabel("Page image").setInputFiles({
    name: "mangasensei-synthetic-page.png",
    mimeType: "image/png",
    buffer: uploadPng,
  });
  await page.getByRole("button", { name: "Analyze page" }).click();
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible({ timeout: 10_000 });
}

export async function uploadDocument(page: Page): Promise<void> {
  await page.getByLabel("Page image").setInputFiles([
    { name: "page-one.png", mimeType: "image/png", buffer: uploadPng },
    { name: "page-two.png", mimeType: "image/png", buffer: uploadPng },
  ]);
  await page.getByRole("button", { name: "Analyze 2 pages" }).click();
  await expect(page.getByText("Page 1 of 2")).toBeVisible({ timeout: 10_000 });
}
