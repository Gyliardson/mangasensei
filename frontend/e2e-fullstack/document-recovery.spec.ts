import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

const redPage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAnUlEQVR4nO3PgQ0AEADAMPz/M1+Q1HrBNvf4y3odcFvDuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6w707AHv8mafmgAAAABJRU5ErkJggg==",
  "base64",
);
const bluePage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAoklEQVR4nO3PAQ3AIADAMEAS/gUgCxcn2VsF29z7jD9ZrwO+ZrjOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneG6C8/BAhzZIzRnAAAAAElFTkSuQmCC",
  "base64",
);
const retryFixturePage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAnklEQVR4nO3PgQ0AEADAMPz/M1+Q1HrBNsceX1mvA25rWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYd0B8+0B7+/9iFoAAAAASUVORK5CYII=",
  "base64",
);

interface DocumentProgress {
  readonly totalPages: number;
  readonly completedPages: number;
  readonly processingPages: number;
  readonly failedPages: number;
  readonly cancelledPages: number;
}

interface DocumentPageSummary {
  readonly pageId: string;
  readonly ordinal: number;
  readonly status: string;
  readonly resultAvailable: boolean;
}

interface DocumentSnapshot {
  readonly documentId: string;
  readonly orderRevision: number;
  readonly status: "processing" | "completed" | "completed_with_errors" | "cancelled";
  readonly pages: readonly DocumentPageSummary[];
  readonly progress: DocumentProgress;
}

interface DocumentUploadEnvelope {
  readonly data: DocumentSnapshot & {
    readonly capabilities: {
      readonly readDocument: string;
      readonly readDocumentImage: string;
      readonly reprocessDocument: string;
      readonly manageDocument: string;
    };
  };
}

interface DocumentSnapshotEnvelope {
  readonly data: DocumentSnapshot;
}

async function useEnglishUi(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
}

async function uploadTwoPages(
  page: Page,
  first: { readonly name: string; readonly buffer: Buffer },
  second: { readonly name: string; readonly buffer: Buffer },
): Promise<DocumentUploadEnvelope["data"]> {
  await page.getByLabel("Page image").setInputFiles([
    { name: first.name, mimeType: "image/png", buffer: first.buffer },
    { name: second.name, mimeType: "image/png", buffer: second.buffer },
  ]);
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/v1/documents",
  );
  await page.getByRole("button", { name: "Analyze 2 pages" }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(202);
  const envelope = (await response.json()) as DocumentUploadEnvelope;
  expect(envelope.data.capabilities.manageDocument).toBeTruthy();
  return envelope.data;
}

async function readDocument(
  request: APIRequestContext,
  documentId: string,
  readDocumentToken: string,
): Promise<DocumentSnapshot> {
  const response = await request.get(`/api/v1/documents/${documentId}`, {
    headers: { "X-Document-Token": readDocumentToken },
  });
  expect(response.ok()).toBe(true);
  return ((await response.json()) as DocumentSnapshotEnvelope).data;
}

async function waitForDocument(
  request: APIRequestContext,
  documentId: string,
  readDocumentToken: string,
  predicate: (snapshot: DocumentSnapshot) => boolean,
  timeoutMs = 30_000,
): Promise<DocumentSnapshot> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const snapshot = await readDocument(request, documentId, readDocumentToken);
    if (predicate(snapshot)) return snapshot;
    // Keep test polling below the production request-rate envelope while the UI polls independently.
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error("document state did not satisfy the expected predicate");
}

test("recovers one terminally failed page without recomputing its readable sibling and persists reorder", async ({
  page,
  request,
}) => {
  await useEnglishUi(page);
  const document = await uploadTwoPages(
    page,
    { name: "readable.png", buffer: redPage },
    { name: "retry-fixture.png", buffer: retryFixturePage },
  );
  const readablePageId = document.pages[0]?.pageId;
  const failedPageId = document.pages[1]?.pageId;
  expect(readablePageId).toBeTruthy();
  expect(failedPageId).toBeTruthy();

  const terminal = await waitForDocument(
    request,
    document.documentId,
    document.capabilities.readDocument,
    (snapshot) => snapshot.status === "completed_with_errors",
  );
  expect(terminal.progress).toEqual({
    totalPages: 2,
    completedPages: 1,
    processingPages: 0,
    failedPages: 1,
    cancelledPages: 0,
  });
  expect(terminal.pages[0]).toMatchObject({
    pageId: readablePageId,
    status: "completed",
    resultAvailable: true,
  });
  expect(terminal.pages[1]).toMatchObject({
    pageId: failedPageId,
    status: "failed",
    resultAvailable: false,
  });
  await expect(page.getByText("Document complete with errors")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Page 1: readable" })).toHaveAttribute(
    "data-page-status",
    "readable",
  );
  await expect(page.getByRole("button", { name: "Page 2: failed" })).toHaveAttribute(
    "data-page-status",
    "failed",
  );
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible();

  const retryResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/v1/documents/${document.documentId}/retry-failed`,
  );
  await page.getByRole("button", { name: "Retry failed pages" }).click();
  const retryResponse = await retryResponsePromise;
  expect(retryResponse.status()).toBe(202);
  expect(await retryResponse.request().headerValue("x-document-token")).toBe(
    document.capabilities.manageDocument,
  );

  const retrying = await waitForDocument(
    request,
    document.documentId,
    document.capabilities.readDocument,
    (snapshot) => snapshot.status === "processing" && snapshot.progress.processingPages === 1,
  );
  expect(retrying.pages[0]).toMatchObject({
    pageId: readablePageId,
    resultAvailable: true,
  });
  await expect(page.getByRole("button", { name: "Page 1: readable" })).toHaveAttribute(
    "data-page-status",
    "readable",
  );
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible();

  const recovered = await waitForDocument(
    request,
    document.documentId,
    document.capabilities.readDocument,
    (snapshot) => snapshot.status === "completed" && snapshot.progress.completedPages === 2,
  );
  expect(recovered.progress).toEqual({
    totalPages: 2,
    completedPages: 2,
    processingPages: 0,
    failedPages: 0,
    cancelledPages: 0,
  });
  await expect(page.getByRole("button", { name: "Page 2: readable" })).toHaveAttribute(
    "data-page-status",
    "readable",
    { timeout: 15_000 },
  );

  const reorderResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "PUT"
      && new URL(response.url()).pathname === `/api/v1/documents/${document.documentId}/order`,
  );
  await page.getByRole("button", { name: "Move page later" }).click();
  const reorderResponse = await reorderResponsePromise;
  expect(reorderResponse.status()).toBe(200);
  expect(await reorderResponse.request().headerValue("x-document-token")).toBe(
    document.capabilities.manageDocument,
  );
  await expect(page.getByText("Page 2 of 2")).toBeVisible();

  const reloaded = await readDocument(
    request,
    document.documentId,
    document.capabilities.readDocument,
  );
  expect(reloaded.orderRevision).toBe(2);
  expect(reloaded.pages.map((entry) => entry.pageId)).toEqual([failedPageId, readablePageId]);
  expect(reloaded.pages.map((entry) => entry.ordinal)).toEqual([0, 1]);
});

test("server cancellation leaves a completed sibling readable and persists terminal truth", async ({
  page,
  request,
}) => {
  await useEnglishUi(page);
  const document = await uploadTwoPages(
    page,
    { name: "completed-first.png", buffer: redPage },
    { name: "unfinished-second.png", buffer: bluePage },
  );
  const completedPageId = document.pages[0]?.pageId;
  const unfinishedPageId = document.pages[1]?.pageId;
  expect(completedPageId).toBeTruthy();
  expect(unfinishedPageId).toBeTruthy();

  // The reader already polls the aggregate. Wait for the completed sibling through the real UI,
  // then cancel immediately instead of duplicating that polling through APIRequestContext.
  await expect(page.getByRole("button", { name: "Page 1: readable" })).toHaveAttribute(
    "data-page-status",
    "readable",
    { timeout: 15_000 },
  );
  await expect(page.getByRole("button", { name: "Page 2: processing" })).toHaveAttribute(
    "data-page-status",
    "processing",
  );
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible();

  const cancelResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/v1/documents/${document.documentId}/cancel`,
  );
  await page.getByRole("button", { name: "Cancel processing" }).click();
  const cancelResponse = await cancelResponsePromise;
  expect(cancelResponse.status()).toBe(200);
  expect(await cancelResponse.request().headerValue("x-document-token")).toBe(
    document.capabilities.manageDocument,
  );
  const cancelled = ((await cancelResponse.json()) as DocumentSnapshotEnvelope).data;
  expect(cancelled.status).toBe("cancelled");
  expect(cancelled.progress).toEqual({
    totalPages: 2,
    completedPages: 1,
    processingPages: 0,
    failedPages: 0,
    cancelledPages: 1,
  });
  expect(cancelled.pages[0]).toMatchObject({
    pageId: completedPageId,
    resultAvailable: true,
  });
  expect(cancelled.pages[1]).toMatchObject({
    pageId: unfinishedPageId,
    status: "cancelled",
    resultAvailable: false,
  });

  await expect(page.getByText("Document processing cancelled")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Page 1: readable" })).toHaveAttribute(
    "data-page-status",
    "readable",
  );
  await expect(page.getByRole("button", { name: "Page 2: cancelled" })).toHaveAttribute(
    "data-page-status",
    "cancelled",
  );
  await expect(page.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible();

  // One fresh server read proves cancellation is durable rather than only client-side state.
  const reloaded = await readDocument(
    request,
    document.documentId,
    document.capabilities.readDocument,
  );
  expect(reloaded.status).toBe("cancelled");
  expect(reloaded.progress.cancelledPages).toBe(1);
  expect(reloaded.pages[0]?.resultAvailable).toBe(true);
  expect(reloaded.pages[1]?.status).toBe("cancelled");
});