import AxeBuilder from "@axe-core/playwright";
import { readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { expect, test, type Page, type Response as PlaywrightResponse } from "@playwright/test";

interface DocumentProgress {
  readonly totalPages: number;
  readonly completedPages: number;
  readonly processingPages: number;
  readonly failedPages: number;
  readonly cancelledPages: number;
}

interface DocumentEnvelope {
  readonly data: {
    readonly documentId: string;
    readonly pages: readonly {
      readonly pageId: string;
      readonly ordinal: number;
      readonly status: string;
      readonly resultAvailable: boolean;
    }[];
    readonly progress: DocumentProgress;
    readonly capabilities: {
      readonly readDocument: string;
      readonly readDocumentImage: string;
    };
  };
}

interface DocumentSnapshotEnvelope {
  readonly data: {
    readonly progress: DocumentProgress;
    readonly pages: readonly {
      readonly pageId: string;
      readonly ordinal: number;
      readonly resultAvailable: boolean;
    }[];
  };
}

interface ProgressObservation extends DocumentProgress {
  readonly observedAtMs: number;
}

interface BlobLifecycle {
  readonly created: string[];
  readonly revoked: string[];
  readonly active: string[];
}

interface ProtectedRead {
  readonly kind: "study-page" | "image";
  readonly pageId: string;
  readonly status: number;
  readonly authorized: boolean;
}

const PAGE_COUNT = 200;
const PAGE_1_RGB = [0, 0, 0];
const PAGE_200_RGB = [199, 191, 97];

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required for the large-document harness`);
  return value;
}

function assertProgressPartition(progress: DocumentProgress): void {
  expect(progress.totalPages).toBe(PAGE_COUNT);
  expect(
    progress.completedPages
      + progress.processingPages
      + progress.failedPages
      + progress.cancelledPages,
  ).toBe(PAGE_COUNT);
}

async function expectBlobImageRendered(page: Page): Promise<void> {
  const image = page.locator(".page-canvas img");
  await expect(image).toHaveAttribute("src", /^blob:/);
  await expect.poll(() =>
    image.evaluate((element) => {
      const rendered = element as HTMLImageElement;
      return rendered.complete && rendered.naturalWidth > 0 && rendered.naturalHeight > 0;
    }),
  ).toBe(true);
}

async function renderedFirstPixel(page: Page): Promise<number[]> {
  return page.locator(".page-canvas img").evaluate((element) => {
    const image = element as HTMLImageElement;
    const canvas = document.createElement("canvas");
    canvas.width = 1;
    canvas.height = 1;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("2d canvas context is unavailable");
    context.drawImage(image, 0, 0, image.naturalWidth, image.naturalHeight, 0, 0, 1, 1);
    return Array.from(context.getImageData(0, 0, 1, 1).data.slice(0, 3));
  });
}

async function axeResult(page: Page): Promise<{ violations: number; seriousCritical: number }> {
  const results = await new AxeBuilder({ page }).analyze();
  const seriousCritical = results.violations.filter(
    (violation) => violation.impact === "serious" || violation.impact === "critical",
  );
  expect(seriousCritical).toEqual([]);
  return {
    violations: results.violations.length,
    seriousCritical: seriousCritical.length,
  };
}

function responsePath(response: PlaywrightResponse): string {
  return new URL(response.url()).pathname;
}

test("CONTROL_PLANE_MAX_200 completes through the real document control plane", async ({
  page,
}) => {
  test.setTimeout(150_000);
  const inputDirectory = requiredEnv("MANGASENSEI_LARGE_DOCUMENT_INPUT");
  const markerPath = requiredEnv("MANGASENSEI_LARGE_DOCUMENT_MARKER");
  const browserMetricsPath = requiredEnv("MANGASENSEI_LARGE_DOCUMENT_BROWSER_METRICS");
  const filenames = (await readdir(inputDirectory))
    .filter((name) => /^page-\d{6}\.png$/.test(name))
    .sort();
  expect(filenames).toHaveLength(PAGE_COUNT);
  const inputPaths = filenames.map((name) => join(inputDirectory, name));

  await page.addInitScript(() => {
    type LargeDocumentWindow = typeof window & {
      __largeDocumentBlobLifecycle?: {
        created: string[];
        revoked: string[];
        active: string[];
      };
      __largeDocumentDelayedPageId?: string | null;
    };
    const target = window as LargeDocumentWindow;
    const lifecycle = { created: [] as string[], revoked: [] as string[], active: [] as string[] };
    target.__largeDocumentBlobLifecycle = lifecycle;
    target.__largeDocumentDelayedPageId = null;

    const originalCreateObjectURL = URL.createObjectURL.bind(URL);
    const originalRevokeObjectURL = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (blob: Blob | MediaSource): string => {
      const url = originalCreateObjectURL(blob);
      lifecycle.created.push(url);
      lifecycle.active.push(url);
      return url;
    };
    URL.revokeObjectURL = (url: string): void => {
      lifecycle.revoked.push(url);
      const index = lifecycle.active.indexOf(url);
      if (index >= 0) lifecycle.active.splice(index, 1);
      originalRevokeObjectURL(url);
    };

    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const response = await originalFetch(input, init);
      const delayedPageId = target.__largeDocumentDelayedPageId;
      const inputUrl = typeof input === "string"
        ? input
        : input instanceof Request
          ? input.url
          : input.toString();
      if (delayedPageId && new URL(inputUrl, window.location.href).pathname.includes(
        `/pages/${delayedPageId}`,
      )) {
        await new Promise((resolve) => window.setTimeout(resolve, 350));
      }
      return response;
    };
  });

  const apiRequests: { method: string; path: string }[] = [];
  const protectedReads: ProtectedRead[] = [];
  const progressObservations: ProgressObservation[] = [];
  const responseTasks: Promise<void>[] = [];
  let unexpected429s = 0;
  let documentId: string | null = null;
  let readDocument: string | null = null;
  let readDocumentImage: string | null = null;

  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/")) apiRequests.push({ method: request.method(), path });
  });
  page.on("response", (response) => {
    if (response.status() === 429) unexpected429s += 1;
    if (!documentId) return;
    const path = responsePath(response);
    const method = response.request().method();
    if (method === "GET" && path === `/api/v1/documents/${documentId}`) {
      responseTasks.push((async () => {
        if (!response.ok()) return;
        const payload = (await response.json()) as DocumentSnapshotEnvelope;
        assertProgressPartition(payload.data.progress);
        progressObservations.push({
          ...payload.data.progress,
          observedAtMs: Date.now(),
        });
      })());
      return;
    }
    const match = path.match(
      new RegExp(`^/api/v1/documents/${documentId}/pages/([^/]+)(/image)?$`),
    );
    if (!match?.[1] || method !== "GET") return;
    const pageId = match[1];
    const kind = match[2] ? "image" : "study-page";
    responseTasks.push((async () => {
      const token = await response.request().headerValue("x-document-token");
      protectedReads.push({
        kind,
        pageId,
        status: response.status(),
        authorized: token === (kind === "image" ? readDocumentImage : readDocument),
      });
    })());
  });

  const flushResponseTasks = async (): Promise<void> => {
    while (responseTasks.length > 0) {
      const batch = responseTasks.splice(0);
      await Promise.all(batch);
    }
  };

  const scenarioStartedAt = Date.now();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
  await page.getByLabel("Page image").setInputFiles(inputPaths);

  const selectedNames = page.locator(".selected-page-name");
  await expect(selectedNames).toHaveCount(PAGE_COUNT);
  await expect(selectedNames.first()).toHaveText("1 page-000001.png");
  await expect(selectedNames.last()).toHaveText("200 page-000200.png");

  const uploadStartedAt = Date.now();
  const uploadResponsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === "POST"
      && responsePath(response) === "/api/v1/documents",
  );
  await page.getByRole("button", { name: "Analyze 200 pages" }).click();
  const uploadResponse = await uploadResponsePromise;
  const admissionAt = Date.now();
  expect(uploadResponse.status()).toBe(202);
  const uploaded = (await uploadResponse.json()) as DocumentEnvelope;
  documentId = uploaded.data.documentId;
  readDocument = uploaded.data.capabilities.readDocument;
  readDocumentImage = uploaded.data.capabilities.readDocumentImage;
  expect(uploaded.data.pages.map((entry) => entry.ordinal)).toEqual(
    Array.from({ length: PAGE_COUNT }, (_, index) => index),
  );
  expect(uploaded.data.progress).toEqual({
    totalPages: PAGE_COUNT,
    completedPages: 0,
    processingPages: PAGE_COUNT,
    failedPages: 0,
    cancelledPages: 0,
  });

  await writeFile(
    markerPath,
    `${JSON.stringify({
      schemaVersion: 1,
      documentId,
      admissionElapsedMs: admissionAt - uploadStartedAt,
    }, null, 2)}\n`,
    "utf8",
  );

  const pageIndex = page.locator(".document-page-index button");
  await expect(pageIndex).toHaveCount(PAGE_COUNT);
  await expect(pageIndex.nth(0)).toHaveAttribute("aria-current", "page");
  await expect(pageIndex.nth(0)).toHaveAttribute("data-page-status", "readable", {
    timeout: 120_000,
  });
  await expectBlobImageRendered(page);
  expect(await renderedFirstPixel(page)).toEqual(PAGE_1_RGB);

  await expect.poll(async () => {
    await flushResponseTasks();
    return progressObservations.some(
      (progress) => progress.completedPages > 0 && progress.processingPages > 0,
    );
  }).toBe(true);
  const partial = progressObservations.find(
    (progress) => progress.completedPages > 0 && progress.processingPages > 0,
  );
  expect(partial).toBeTruthy();

  const firstBlobState = await page.evaluate(() => {
    const target = window as typeof window & { __largeDocumentBlobLifecycle?: BlobLifecycle };
    return target.__largeDocumentBlobLifecycle;
  });
  expect(firstBlobState?.active).toHaveLength(1);

  await expect(
    page.getByText("200 / 200 pages readable · 0 processing · 0 failed · 0 cancelled"),
  ).toBeVisible({ timeout: 120_000 });
  const completionAt = Date.now();
  expect(completionAt - admissionAt).toBeLessThanOrEqual(120_000);

  await flushResponseTasks();
  expect(progressObservations.at(-1)).toMatchObject({
    totalPages: PAGE_COUNT,
    completedPages: PAGE_COUNT,
    processingPages: 0,
    failedPages: 0,
    cancelledPages: 0,
  });
  for (const progress of progressObservations) assertProgressPartition(progress);

  const selectorOrdinals = await pageIndex.locator("span").allTextContents();
  expect(selectorOrdinals).toEqual(
    Array.from({ length: PAGE_COUNT }, (_, index) => String(index + 1)),
  );
  expect(await pageIndex.evaluateAll((buttons) =>
    buttons.map((button) => button.getAttribute("data-page-status")),
  )).toEqual(Array.from({ length: PAGE_COUNT }, () => "readable"));

  const desktopA11y = await axeResult(page);

  const page1Id = uploaded.data.pages[0]?.pageId;
  const page100Id = uploaded.data.pages[99]?.pageId;
  const page200Id = uploaded.data.pages[199]?.pageId;
  expect(page1Id).toBeTruthy();
  expect(page100Id).toBeTruthy();
  expect(page200Id).toBeTruthy();

  await page.evaluate((delayedPageId) => {
    const target = window as typeof window & { __largeDocumentDelayedPageId?: string | null };
    target.__largeDocumentDelayedPageId = delayedPageId;
  }, page100Id!);

  const page100Study = page.waitForResponse(
    (response) =>
      response.request().method() === "GET"
      && responsePath(response) === `/api/v1/documents/${documentId}/pages/${page100Id}`,
  );
  const page100Image = page.waitForResponse(
    (response) =>
      response.request().method() === "GET"
      && responsePath(response)
        === `/api/v1/documents/${documentId}/pages/${page100Id}/image`,
  );
  await pageIndex.nth(99).focus();
  await expect(pageIndex.nth(99)).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Page 100 of 200")).toBeVisible();
  const [page100StudyResponse, page100ImageResponse] = await Promise.all([
    page100Study,
    page100Image,
  ]);
  expect(page100StudyResponse.status()).toBe(200);
  expect(page100ImageResponse.status()).toBe(200);
  expect(await page100StudyResponse.request().headerValue("x-document-token")).toBe(readDocument);
  expect(await page100ImageResponse.request().headerValue("x-document-token")).toBe(
    readDocumentImage,
  );

  const page200Study = page.waitForResponse(
    (response) =>
      response.request().method() === "GET"
      && responsePath(response) === `/api/v1/documents/${documentId}/pages/${page200Id}`,
  );
  const page200Image = page.waitForResponse(
    (response) =>
      response.request().method() === "GET"
      && responsePath(response)
        === `/api/v1/documents/${documentId}/pages/${page200Id}/image`,
  );
  await pageIndex.nth(199).focus();
  await expect(pageIndex.nth(199)).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Page 200 of 200")).toBeVisible();
  const [page200StudyResponse, page200ImageResponse] = await Promise.all([
    page200Study,
    page200Image,
  ]);
  expect(page200StudyResponse.status()).toBe(200);
  expect(page200ImageResponse.status()).toBe(200);
  expect(await page200StudyResponse.request().headerValue("x-document-token")).toBe(readDocument);
  expect(await page200ImageResponse.request().headerValue("x-document-token")).toBe(
    readDocumentImage,
  );
  await expectBlobImageRendered(page);
  expect(await renderedFirstPixel(page)).toEqual(PAGE_200_RGB);

  await page.waitForTimeout(450);
  await expect(page.getByText("Page 200 of 200")).toBeVisible();
  expect(await renderedFirstPixel(page)).toEqual(PAGE_200_RGB);

  const finalBlobState = await page.evaluate(() => {
    const target = window as typeof window & { __largeDocumentBlobLifecycle?: BlobLifecycle };
    return target.__largeDocumentBlobLifecycle;
  });
  expect(finalBlobState?.active).toHaveLength(1);
  expect(finalBlobState?.created.every((url) => (
    finalBlobState.active.includes(url) || finalBlobState.revoked.includes(url)
  ))).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(pageIndex).toHaveCount(PAGE_COUNT);
  await expect(page.getByText("Page 200 of 200")).toBeVisible();
  const mobileA11y = await axeResult(page);

  await flushResponseTasks();
  const uploadPosts = apiRequests.filter(
    (request) => request.method === "POST" && request.path === "/api/v1/documents",
  );
  const aggregateGets = apiRequests.filter(
    (request) => request.method === "GET"
      && request.path === `/api/v1/documents/${documentId}`,
  );
  const studyPageGets = apiRequests.filter(
    (request) =>
      request.method === "GET"
      && new RegExp(`^/api/v1/documents/${documentId}/pages/[^/]+$`).test(request.path),
  );
  const imageGets = apiRequests.filter(
    (request) =>
      request.method === "GET"
      && new RegExp(`^/api/v1/documents/${documentId}/pages/[^/]+/image$`).test(request.path),
  );
  expect(uploadPosts).toHaveLength(1);
  expect(aggregateGets.length).toBeLessThanOrEqual(60);
  expect(studyPageGets).toHaveLength(3);
  expect(imageGets).toHaveLength(3);
  expect(apiRequests.length).toBe(
    uploadPosts.length + aggregateGets.length + studyPageGets.length + imageGets.length,
  );
  expect(apiRequests.length).toBeLessThanOrEqual(67);
  expect(unexpected429s).toBe(0);

  const sampledPageIds = new Set([page1Id!, page100Id!, page200Id!]);
  const studyReadPageIds = new Set(
    protectedReads.filter((read) => read.kind === "study-page").map((read) => read.pageId),
  );
  const imageReadPageIds = new Set(
    protectedReads.filter((read) => read.kind === "image").map((read) => read.pageId),
  );
  expect(studyReadPageIds).toEqual(sampledPageIds);
  expect(imageReadPageIds).toEqual(sampledPageIds);
  expect(protectedReads.every((read) => read.status === 200 && read.authorized)).toBe(true);

  await page.getByRole("button", { name: "New page" }).click();
  await expect(page.getByLabel("Page image")).toBeVisible();
  const unmountedBlobState = await page.evaluate(() => {
    const target = window as typeof window & { __largeDocumentBlobLifecycle?: BlobLifecycle };
    return target.__largeDocumentBlobLifecycle;
  });
  expect(unmountedBlobState?.active).toHaveLength(0);
  expect(unmountedBlobState?.created.every((url) => unmountedBlobState.revoked.includes(url))).toBe(
    true,
  );

  const totalScenarioElapsedMs = Date.now() - scenarioStartedAt;
  await writeFile(
    browserMetricsPath,
    `${JSON.stringify({
      schemaVersion: 1,
      workload: "CONTROL_PLANE_MAX_200",
      documentId,
      timing: {
        admissionElapsedMs: admissionAt - uploadStartedAt,
        processingElapsedMs: completionAt - admissionAt,
        totalScenarioElapsedMs,
      },
      progress: {
        partial,
        final: progressObservations.at(-1),
        observationCount: progressObservations.length,
        partitionsAll200: progressObservations.every((progress) => (
          progress.completedPages
            + progress.processingPages
            + progress.failedPages
            + progress.cancelledPages
        ) === PAGE_COUNT),
      },
      requests: {
        browserApiTotal: apiRequests.length,
        uploadPosts: uploadPosts.length,
        aggregateGets: aggregateGets.length,
        studyPageGets: studyPageGets.length,
        imageGets: imageGets.length,
        unexpected429s,
        sampledStudyPageIds: [...studyReadPageIds].sort(),
        sampledImagePageIds: [...imageReadPageIds].sort(),
      },
      blobLifecycle: {
        created: unmountedBlobState?.created.length ?? 0,
        revoked: unmountedBlobState?.revoked.length ?? 0,
        activeAfterUnmount: unmountedBlobState?.active.length ?? -1,
        allCreatedRevokedAfterUnmount: unmountedBlobState?.created.every(
          (url) => unmountedBlobState.revoked.includes(url),
        ) ?? false,
        staleNavigationStayedOnPage200: true,
      },
      browser: {
        desktop: {
          viewport: { width: 1440, height: 1000 },
          accessibility: desktopA11y,
        },
        mobile: {
          viewport: { width: 390, height: 844 },
          accessibility: mobileA11y,
        },
        keyboardNavigation: ["page-100:Enter", "page-200:Enter"],
      },
    }, null, 2)}\n`,
    "utf8",
  );
});