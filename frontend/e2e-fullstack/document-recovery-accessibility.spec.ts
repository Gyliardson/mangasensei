import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { access, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const redPage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAnUlEQVR4nO3PgQ0AEADAMPz/M1+Q1HrBNvf4y3odcFvDuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6w707AHv8mafmgAAAABJRU5ErkJggg==",
  "base64",
);
const bluePage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAoklEQVR4nO3PAQ3AIADAMEAS/gUgCxcn2VsF29z7jD9ZrwO+ZrjOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneG6C8/BAhzZIzRnAAAAAElFTkSuQmCC",
  "base64",
);
const documentCancelHoldPath = join(tmpdir(), "mangasensei-document-cancel.hold");
const documentCancelReadyPath = join(tmpdir(), "mangasensei-document-cancel.ready");

interface DocumentEnvelope {
  readonly data: {
    readonly documentId: string;
    readonly status: "processing" | "completed" | "completed_with_errors" | "cancelled";
    readonly progress: {
      readonly completedPages: number;
      readonly processingPages: number;
      readonly cancelledPages: number;
    };
    readonly capabilities: {
      readonly readDocument: string;
      readonly manageDocument: string;
    };
  };
}

interface CancellationEnvelope {
  readonly data: {
    readonly cancelledPages: number;
    readonly cancelRequestedPages: number;
    readonly status: "processing" | "completed" | "completed_with_errors" | "cancelled";
    readonly progress: {
      readonly completedPages: number;
      readonly processingPages: number;
      readonly cancelledPages: number;
    };
  };
}

async function useEnglishUi(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function unlinkIfExists(path: string): Promise<void> {
  await unlink(path).catch(() => undefined);
}

test("Document recovery controls remain usable and axe-clean on a mobile viewport", async ({ page }) => {
  await unlinkIfExists(documentCancelReadyPath);
  await unlinkIfExists(documentCancelHoldPath);
  await writeFile(documentCancelHoldPath, "hold", "utf8");

  try {
    await page.setViewportSize({ width: 390, height: 844 });
    await useEnglishUi(page);

    const uploadResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST"
        && new URL(response.url()).pathname === "/api/v1/documents",
    );
    await page.getByLabel("Page image").setInputFiles([
      { name: "completed.png", mimeType: "image/png", buffer: redPage },
      { name: "unfinished.png", mimeType: "image/png", buffer: bluePage },
    ]);
    await page.getByRole("button", { name: "Analyze 2 pages" }).click();
    const uploaded = (await (await uploadResponsePromise).json()) as DocumentEnvelope;

    const beforeCancel = await new AxeBuilder({ page }).analyze();
    expect(beforeCancel.violations).toEqual([]);

    await expect
      .poll(() => pathExists(documentCancelReadyPath), { timeout: 15_000 })
      .toBe(true);

    await expect
      .poll(async () => {
        const response = await page.request.get(
          `/api/v1/documents/${uploaded.data.documentId}`,
          { headers: { "X-Document-Token": uploaded.data.capabilities.readDocument } },
        );
        const snapshot = (await response.json()) as DocumentEnvelope;
        return snapshot.data.progress;
      }, { timeout: 15_000 })
      .toMatchObject({ completedPages: 1, processingPages: 1 });

    await expect(page.getByRole("button", { name: "Cancel processing" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Move page later" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Page 1: readable" })).toBeVisible();

    const cancelResponsePromise = page.waitForResponse((response) =>
      response.request().method() === "POST"
        && new URL(response.url()).pathname
          === `/api/v1/documents/${uploaded.data.documentId}/cancel`,
    );
    await page.getByRole("button", { name: "Cancel processing" }).click();
    const cancelResponse = await cancelResponsePromise;
    expect(cancelResponse.status()).toBe(200);
    const cancellation = (await cancelResponse.json()) as CancellationEnvelope;
    expect(cancellation.data).toMatchObject({
      cancelledPages: 0,
      cancelRequestedPages: 1,
      status: "processing",
      progress: {
        completedPages: 1,
        processingPages: 1,
        cancelledPages: 0,
      },
    });

    await unlinkIfExists(documentCancelHoldPath);
    await expect(page.getByText("Document processing cancelled")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "Page 2: cancelled" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Page 1: readable" })).toBeVisible();

    const afterCancel = await new AxeBuilder({ page }).analyze();
    expect(afterCancel.violations).toEqual([]);
  } finally {
    await unlinkIfExists(documentCancelHoldPath);
    await unlinkIfExists(documentCancelReadyPath);
  }
});
