import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { documentMessagesFor } from "./lib/documentUiMessages";
import { messagesFor } from "./lib/uiMessages";

function documentData(sourceKind: "images" | "pdf" = "images", totalPages = 2) {
  return {
    documentId: "document-001",
    sourceKind,
    expiresAt: "2026-08-12T00:00:00Z",
    orderRevision: 1,
    status: "processing",
    pages: Array.from({ length: totalPages }, (_, ordinal) => ({
      pageId: `page-${ordinal + 1}`,
      ordinal,
      status: "pending",
      resultAvailable: false,
    })),
    progress: {
      totalPages,
      completedPages: 0,
      processingPages: totalPages,
      failedPages: 0,
      cancelledPages: 0,
    },
    capabilities: {
      readDocument: "read-document-token",
      readDocumentImage: "read-document-image-token",
      reprocessDocument: "reprocess-document-token",
      manageDocument: "manage-document-token",
    },
  };
}

async function selectTwoImages(user: ReturnType<typeof userEvent.setup>) {
  const first = new File(["first"], "z-first.png", { type: "image/png" });
  const second = new File(["second"], "a-second.png", { type: "image/png" });
  await user.upload(screen.getByLabelText(/Imagem da página/), [first, second]);
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
    let capturedImages: FormDataEntryValue[] = [];
    let capturedStudyLanguages: FormDataEntryValue[] = [];
    let capturedDictionaryLanguage = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === "/api/v1/documents") {
        const form = init?.body as FormData;
        capturedImages = form.getAll("images[]");
        capturedStudyLanguages = form.getAll("studyLanguage");
        capturedDictionaryLanguage = form.has("dictionaryLanguage");
        return Response.json({ success: true, data: documentData(), error: null }, { status: 202 });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const { first, second } = await selectTwoImages(user);

    await user.click(screen.getByRole("button", { name: "Analisar 2 páginas" }));

    expect(await screen.findByText("Página 1 de 2")).toBeVisible();
    expect(capturedImages).toEqual([first, second]);
    expect(capturedStudyLanguages).toEqual(["pt-BR"]);
    expect(capturedDictionaryLanguage).toBe(false);
  });

  it("shows a truthful PDF import phase and then reuses the Document reader", async () => {
    const user = userEvent.setup();
    const pdf = new File(["%PDF-1.7\n"], "chapter.pdf", { type: "application/pdf" });
    const importCreated = {
      importId: "import-001",
      sourceKind: "pdf",
      status: "queued",
      rasterContract: "pdfium-raster-v1",
      expiresAt: "2026-08-12T00:00:00Z",
      capabilities: { readDocumentImport: "import-read-token" },
    };
    const document = documentData("pdf", 1);
    const importCompleted = {
      ...importCreated,
      status: "completed",
      pageCount: 1,
      errorCode: null,
      createdAt: "2026-08-11T00:00:00Z",
      document: { documentId: document.documentId, capabilities: document.capabilities },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/document-imports" && init?.method === "POST") {
        expect((init.body as FormData).get("pdf")).toBe(pdf);
        return Response.json({ success: true, data: importCreated, error: null }, { status: 202 });
      }
      if (url === "/api/v1/document-imports/import-001") {
        expect(init?.headers).toEqual({ "X-Document-Import-Token": "import-read-token" });
        return Response.json({ success: true, data: importCompleted, error: null });
      }
      if (url === "/api/v1/documents/document-001") {
        return Response.json({ success: true, data: document, error: null });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000138"),
      getRandomValues: vi.fn(),
    });
    render(<App />);

    await user.upload(screen.getByLabelText(/Imagem da página/), pdf);
    expect(screen.getByText("1 PDF selecionado")).toBeVisible();
    expect(screen.getByText(/renderização local acontece antes do OCR/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Analisar 1 página" }));

    expect(await screen.findByText("Página 1 de 1")).toBeVisible();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("rejects mixing a PDF with image pages before network access", async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const pdf = new File(["%PDF-1.7\n"], "chapter.pdf", { type: "application/pdf" });
    const image = new File(["image"], "page.png", { type: "image/png" });

    await user.upload(screen.getByLabelText(/Imagem da página/), [pdf, image]);
    await user.click(screen.getByRole("button", { name: "Analisar 2 páginas" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("pdf_must_be_single");
    expect(fetchMock).not.toHaveBeenCalled();
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
      expect(screen.getByRole("alert")).toHaveTextContent(
        messagesFor("pt-BR").unexpectedProcessingError,
      );
    });
  });
});
