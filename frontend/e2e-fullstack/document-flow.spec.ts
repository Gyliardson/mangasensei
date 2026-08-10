import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const redPage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAoklEQVR4nO3PAQ3AIADAMEAS/gUgCxcn2VsF2zx7jz9ZrwO+ZrjOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneG6C9DtAhwwyYwSAAAAAElFTkSuQmCC",
  "base64",
);
const bluePage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAoklEQVR4nO3PAQ3AIADAMEAS/gUgCxcn2VsF29z7jD9ZrwO+ZrjOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneG6C8/BAhzZIzRnAAAAAElFTkSuQmCC",
  "base64",
);

interface DocumentUploadEnvelope {
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
      readonly reprocessDocument: string;
    };
  };
}

interface DocumentProgress {
  readonly totalPages: number;
  readonly completedPages: number;
  readonly processingPages: number;
  readonly failedPages: number;
}

interface DocumentSnapshotEnvelope {
  readonly data: {
    readonly pages: readonly {
      readonly pageId: string;
      readonly ordinal: number;
      readonly status: string;
      readonly resultAvailable: boolean;
    }[];
    readonly progress: DocumentProgress;
  };
}

interface StudyPageEnvelope {
  readonly data: {
    readonly pageId: string;
    readonly studyLanguage: string;
    readonly requestedDictionaryLanguage?: string;
    readonly resultAvailable: boolean;
    readonly regions: readonly {
      readonly text: string;
      readonly vocabulary: readonly {
        readonly meanings: readonly string[];
      }[];
    }[];
  };
}

async function waitForDocument(
  request: APIRequestContext,
  documentId: string,
  readDocument: string,
  predicate: (snapshot: DocumentSnapshotEnvelope["data"]) => boolean,
): Promise<DocumentSnapshotEnvelope["data"]> {
  const deadline = Date.now() + 20_000;
  while (Date.now() < deadline) {
    const response = await request.get(`/api/v1/documents/${documentId}`, {
      headers: { "X-Document-Token": readDocument },
    });
    expect(response.ok()).toBe(true);
    const payload = (await response.json()) as DocumentSnapshotEnvelope;
    if (predicate(payload.data)) return payload.data;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("document state did not satisfy the expected predicate");
}

function collectDocumentImageBodies(page: Page): Map<string, Promise<Buffer>> {
  const bodies = new Map<string, Promise<Buffer>>();
  page.on("response", (response) => {
    const path = new URL(response.url()).pathname;
    const match = path.match(/^\/api\/v1\/documents\/[^/]+\/pages\/([^/]+)\/image$/);
    if (match?.[1] && response.ok() && !bodies.has(match[1])) {
      bodies.set(match[1], response.body());
    }
  });
  return bodies;
}

async function waitForImageBody(
  bodies: Map<string, Promise<Buffer>>,
  pageId: string,
): Promise<Buffer> {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const body = bodies.get(pageId);
    if (body) return body;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`protected image was not fetched for ${pageId}`);
}

test("creates, partially reads, navigates and reprojects a real multipage document", async ({
  page,
  request,
}) => {
  const protectedImages = collectDocumentImageBodies(page);

  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");

  await page.getByLabel("Page image").setInputFiles([
    { name: "z-red.png", mimeType: "image/png", buffer: redPage },
    { name: "a-blue.png", mimeType: "image/png", buffer: bluePage },
  ]);
  const selectedPages = page.locator(".selected-page-name");
  await expect(selectedPages).toHaveText(["1 z-red.png", "2 a-blue.png"]);

  await page.getByRole("button", { name: "Move a-blue.png up" }).focus();
  await page.keyboard.press("Enter");
  await expect(selectedPages).toHaveText(["1 a-blue.png", "2 z-red.png"]);

  const uploadResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/v1/documents",
  );
  await page.getByRole("button", { name: "Analyze 2 pages" }).click();
  const uploadResponse = await uploadResponsePromise;
  expect(uploadResponse.status()).toBe(202);
  const uploaded = (await uploadResponse.json()) as DocumentUploadEnvelope;
  const document = uploaded.data;
  expect(document.pages.map((entry) => entry.ordinal)).toEqual([0, 1]);
  expect(document.progress).toEqual({
    totalPages: 2,
    completedPages: 0,
    processingPages: 2,
    failedPages: 0,
  });
  expect(document.capabilities.readDocument).toBeTruthy();
  expect(document.capabilities.readDocumentImage).toBeTruthy();
  expect(document.capabilities.reprocessDocument).toBeTruthy();

  const partial = await waitForDocument(
    request,
    document.documentId,
    document.capabilities.readDocument,
    (snapshot) =>
      snapshot.progress.completedPages === 1
      && snapshot.progress.processingPages === 1
      && snapshot.pages[0]?.resultAvailable === true
      && snapshot.pages[1]?.resultAvailable === false,
  );
  expect(partial.progress.failedPages).toBe(0);

  const pageIndex = page.locator(".document-page-index button");
  await expect(pageIndex.nth(0)).toHaveAttribute("aria-current", "page");
  await expect(pageIndex.nth(0)).toHaveAttribute("data-page-status", "readable");
  await expect(pageIndex.nth(1)).toHaveAttribute("data-page-status", "processing");
  await expect(page.getByText("1 / 2 pages complete · 1 processing · 0 failed")).toBeVisible();
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible({
    timeout: 20_000,
  });

  const firstPageId = document.pages[0]?.pageId;
  const secondPageId = document.pages[1]?.pageId;
  expect(firstPageId).toBeTruthy();
  expect(secondPageId).toBeTruthy();
  expect(await waitForImageBody(protectedImages, firstPageId!)).toEqual(bluePage);

  await waitForDocument(
    request,
    document.documentId,
    document.capabilities.readDocument,
    (snapshot) => snapshot.progress.completedPages === 2 && snapshot.progress.processingPages === 0,
  );
  await expect(pageIndex.nth(1)).toHaveAttribute("data-page-status", "readable", { timeout: 10_000 });
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible();
  expect(await waitForImageBody(protectedImages, secondPageId!)).toEqual(redPage);

  const dictionaryMutationPromise = page.waitForResponse((response) => {
    const path = new URL(response.url()).pathname;
    return response.request().method() === "POST"
      && path === `/api/v1/documents/${document.documentId}/pages/${secondPageId}/reprocess`;
  });
  const studyControls = page.getByRole("group", { name: "Study preferences" });
  await studyControls.getByRole("combobox", { name: "Dictionary language" }).selectOption("de");
  const dictionaryMutation = await dictionaryMutationPromise;
  expect(dictionaryMutation.status()).toBe(202);
  expect(dictionaryMutation.request().postDataJSON()).toEqual({ dictionaryLanguage: "de" });
  expect(await dictionaryMutation.request().headerValue("x-document-token")).toBeTruthy();
  await expect(page.getByText("Katze", { exact: true })).toHaveAttribute("lang", "de", {
    timeout: 20_000,
  });

  const untouchedFirstPageResponse = await request.get(
    `/api/v1/documents/${document.documentId}/pages/${firstPageId}`,
    { headers: { "X-Document-Token": document.capabilities.readDocument } },
  );
  expect(untouchedFirstPageResponse.ok()).toBe(true);
  const untouchedFirstPage = (await untouchedFirstPageResponse.json()) as StudyPageEnvelope;
  expect(untouchedFirstPage.data.pageId).toBe(firstPageId);
  expect(untouchedFirstPage.data.studyLanguage).toBe("pt-BR");
  expect(untouchedFirstPage.data.requestedDictionaryLanguage ?? "en").toBe("en");
  expect(untouchedFirstPage.data.resultAvailable).toBe(true);
  expect(untouchedFirstPage.data.regions[0]?.text).toBe("猫です");
  expect(untouchedFirstPage.data.regions[0]?.vocabulary[0]?.meanings).toEqual(["cat"]);

  await page.getByRole("button", { name: "Previous" }).click();
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible();
});
