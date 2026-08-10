import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { documentMessagesFor } from "./lib/documentUiMessages";

function documentData() {
  return {
    documentId: "document-001",
    sourceKind: "images",
    expiresAt: "2026-08-11T00:00:00Z",
    orderRevision: 1,
    pages: [
      { pageId: "page-a", ordinal: 0, status: "pending", resultAvailable: false },
      { pageId: "page-b", ordinal: 1, status: "pending", resultAvailable: false },
    ],
    progress: {
      totalPages: 2,
      completedPages: 0,
      processingPages: 2,
      failedPages: 0,
    },
    capabilities: {
      readDocument: "read-document-token",
      readDocumentImage: "read-document-image-token",
      reprocessDocument: "reprocess-document-token",
    },
  };
}

async function selectTwoImages(user: ReturnType<typeof userEvent.setup>) {
  const first = new File(["first"], "z-first.png", { type: "image/png" });
  const second = new File(["second"], "a-second.png", { type: "image/png" });
  await user.upload(screen.getByLabelText("Imagem da página"), [first, second]);
  return { first, second };
}

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("App document upload", () => {
  it("creates a multi-image document with one study language and ordered image parts", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/v1/documents") {
        return Response.json({ success: true, data: documentData(), error: null }, { status: 202 });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const { first, second } = await selectTwoImages(user);

    await user.click(screen.getByRole("button", { name: "Analisar 2 páginas" }));

    expect(await screen.findByText("Página 1 de 2")).toBeVisible();
    const createCall = fetchMock.mock.calls.find(([input]) => String(input) === "/api/v1/documents");
    expect(createCall).toBeDefined();
    const request = createCall?.[1] as RequestInit;
    const form = request.body as FormData;
    expect(form.getAll("images[]")).toEqual([first, second]);
    expect(form.getAll("studyLanguage")).toEqual(["pt-BR"]);
    expect(form.has("dictionaryLanguage")).toBe(false);
  });

  it.each([
    ["document_page_limit_exceeded", documentMessagesFor("pt-BR").documentPageLimit],
    ["document_byte_limit_exceeded", documentMessagesFor("pt-BR").documentByteLimit],
    ["document_pixel_limit_exceeded", documentMessagesFor("pt-BR").documentPixelLimit],
    ["document_storage_failed", documentMessagesFor("pt-BR").documentUploadFailed],
  ])("renders the localized document error for %s", async (code, expectedMessage) => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { success: false, data: null, error: { code, message: "document upload failed" } },
          { status: 422 },
        ),
      ),
    );
    render(<App />);
    await selectTwoImages(user);

    await user.click(screen.getByRole("button", { name: "Analisar 2 páginas" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage);
  });

  it("renders the generic processing error when document upload throws outside the API contract", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new Error("network exploded"))));
    render(<App />);
    await selectTwoImages(user);

    await user.click(screen.getByRole("button", { name: "Analisar 2 páginas" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(/erro inesperado/i);
    });
  });
});
