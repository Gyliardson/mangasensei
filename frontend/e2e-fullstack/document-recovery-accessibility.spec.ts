import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const redPage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAnUlEQVR4nO3PgQ0AEADAMPz/M1+Q1HrBNvf4y3odcFvDuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6w707AHv8mafmgAAAABJRU5ErkJggg==",
  "base64",
);
const bluePage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAoklEQVR4nO3PAQ3AIADAMEAS/gUgCxcn2VsF29z7jD9ZrwO+ZrjOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneG6C8/BAhzZIzRnAAAAAElFTkSuQmCC",
  "base64",
);

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

async function useEnglishUi(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
}

test("Document recovery controls remain usable and axe-clean on a mobile viewport", async ({ page }) => {
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

  const beforeCancel = await new AxeBuilder({ page }).analyze();
  expect(beforeCancel.violations).toEqual([]);

  await page.getByRole("button", { name: "Cancel processing" }).click();
  await expect(page.getByText("Document processing cancelled")).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("button", { name: "Page 2: cancelled" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Page 1: readable" })).toBeVisible();

  const afterCancel = await new AxeBuilder({ page }).analyze();
  expect(afterCancel.violations).toEqual([]);
});
