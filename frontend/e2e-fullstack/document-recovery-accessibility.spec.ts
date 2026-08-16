import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { createServer, type Socket } from "node:net";

const redPage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAnUlEQVR4nO3PgQ0AEADAMPz/M1+Q1HrBNvf4y3odcFvDuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6xrWNaxrWNewrmFdw7qGdQ3rGtY1rGtY17CuYV3DuoZ1Desa1jWsa1jXsK5hXcO6hnUN6w707AHv8mafmgAAAABJRU5ErkJggg==",
  "base64",
);
const bluePage = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAFAAAAB4CAIAAADqjOKhAAAAoklEQVR4nO3PAQ3AIADAMEAS/gUgCxcn2VsF29z7jD9ZrwO+ZrjOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneE6w3WG6wzXGa4zXGe4znCd4TrDdYbrDNcZrjNcZ7jOcJ3hOsN1husM1xmuM1xnuM5wneG6C8/BAhzZIzRnAAAAAElFTkSuQmCC",
  "base64",
);
const documentCancelBarrierHost = "127.0.0.1";
const documentCancelBarrierPort = 48154;

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

interface CancellationBarrier {
  readonly ready: Promise<void>;
  release(): void;
  close(): Promise<void>;
}

async function useEnglishUi(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("combobox", { name: "Idioma da interface" }).selectOption("en");
}

async function createCancellationBarrier(): Promise<CancellationBarrier> {
  let client: Socket | undefined;
  let resolveReady: (() => void) | undefined;
  let rejectReady: ((reason?: unknown) => void) | undefined;
  const ready = new Promise<void>((resolve, reject) => {
    resolveReady = resolve;
    rejectReady = reject;
  });
  const server = createServer((socket) => {
    if (client) {
      socket.destroy();
      return;
    }
    client = socket;
    let handshake = "";
    const onError = (error: Error): void => rejectReady?.(error);
    const onData = (chunk: Buffer): void => {
      handshake += chunk.toString("utf8");
      if (!handshake.includes("\n")) {
        return;
      }
      socket.off("data", onData);
      socket.off("error", onError);
      if (handshake !== "ready\n") {
        rejectReady?.(new Error(`unexpected cancellation barrier handshake: ${handshake}`));
        return;
      }
      resolveReady?.();
    };
    socket.on("error", onError);
    socket.on("data", onData);
  });

  await new Promise<void>((resolve, reject) => {
    const onError = (error: Error): void => reject(error);
    server.once("error", onError);
    server.listen(documentCancelBarrierPort, documentCancelBarrierHost, () => {
      server.off("error", onError);
      resolve();
    });
  });

  return {
    ready,
    release(): void {
      if (!client) {
        throw new Error("cancellation barrier released before the worker connected");
      }
      client.end();
    },
    async close(): Promise<void> {
      client?.destroy();
      if (!server.listening) {
        return;
      }
      await new Promise<void>((resolve, reject) => {
        server.close((error) => {
          if (error) {
            reject(error);
            return;
          }
          resolve();
        });
      });
    },
  };
}

test("Document recovery controls remain usable and axe-clean on a mobile viewport", async ({ page }) => {
  const cancellationBarrier = await createCancellationBarrier();

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

    await cancellationBarrier.ready;

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

    cancellationBarrier.release();
    await expect(page.getByText("Document processing cancelled")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("button", { name: "Page 2: cancelled" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Page 1: readable" })).toBeVisible();

    const afterCancel = await new AxeBuilder({ page }).analyze();
    expect(afterCancel.violations).toEqual([]);
  } finally {
    await cancellationBarrier.close();
  }
});
