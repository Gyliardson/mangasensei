import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, type DocumentSnapshot } from "./api";
import { uploadPdfImport, waitForPdfImport } from "./pdfImports";

function envelope(data: unknown, status = 200): Response {
  return Response.json({ success: true, data, error: null }, { status });
}

const created = {
  importId: "import-001",
  sourceKind: "pdf" as const,
  status: "queued" as const,
  rasterContract: "pdfium-raster-v1" as const,
  expiresAt: "2026-08-12T12:00:00Z",
  capabilities: { readDocumentImport: "import-read-token" },
};

const capabilities = {
  readDocument: "document-read-token",
  readDocumentImage: "document-image-token",
  reprocessDocument: "document-reprocess-token",
  manageDocument: "document-manage-token",
};

const snapshot: DocumentSnapshot = {
  documentId: "document-001",
  sourceKind: "pdf",
  expiresAt: created.expiresAt,
  orderRevision: 1,
  status: "processing",
  pages: [{ pageId: "page-001", ordinal: 0, status: "pending", resultAvailable: false }],
  progress: {
    totalPages: 1,
    completedPages: 0,
    processingPages: 1,
    failedPages: 0,
    cancelledPages: 0,
  },
};

describe("PDF import API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("submits one PDF with an in-memory idempotency identity", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => envelope(created, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000138"),
      getRandomValues: vi.fn(),
    });
    const file = new File(["%PDF-1.7\n"], "study.pdf", { type: "application/pdf" });

    await expect(
      uploadPdfImport(file, "pt-BR", new AbortController().signal),
    ).resolves.toEqual(created);

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/document-imports");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.headers).toEqual({
      "Idempotency-Key": "pdf-import-00000000-0000-4000-8000-000000000138",
    });
    const form = init.body as FormData;
    expect(form.get("pdf")).toBe(file);
    expect(form.get("studyLanguage")).toBe("pt-BR");
  });

  it("polls the transient import and returns the normal Document access", async () => {
    const view = {
      ...created,
      status: "completed" as const,
      pageCount: 1,
      errorCode: null,
      createdAt: "2026-08-11T12:00:00Z",
      document: { documentId: snapshot.documentId, capabilities },
    };
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(envelope(created, 202))
      .mockResolvedValueOnce(envelope(view))
      .mockResolvedValueOnce(envelope(snapshot));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000139"),
      getRandomValues: vi.fn(),
    });
    const file = new File(["%PDF-1.7\n"], "study.pdf", { type: "application/pdf" });
    const signal = new AbortController().signal;
    const importAccess = await uploadPdfImport(file, "en", signal);
    const statuses: string[] = [];

    await expect(waitForPdfImport(importAccess, signal, (status) => statuses.push(status))).resolves.toEqual({
      ...snapshot,
      capabilities,
    });

    expect(statuses).toEqual(["completed"]);
    expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/v1/document-imports/import-001");
    expect(fetchMock.mock.calls[1]?.[1]?.headers).toEqual({
      "X-Document-Import-Token": "import-read-token",
    });
    expect(fetchMock.mock.calls[2]?.[0]).toBe("/api/v1/documents/document-001");
    expect(fetchMock.mock.calls[2]?.[1]?.headers).toEqual({
      "X-Document-Token": "document-read-token",
    });
  });

  it("surfaces a terminal renderer error without constructing a Document", async () => {
    const failed = {
      ...created,
      status: "failed" as const,
      pageCount: null,
      errorCode: "pdf_encrypted_unsupported",
      createdAt: "2026-08-11T12:00:00Z",
      document: null,
    };
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => envelope(failed)));

    await expect(
      waitForPdfImport(created, new AbortController().signal, vi.fn()),
    ).rejects.toEqual(new ApiError("pdf_encrypted_unsupported"));
  });

  it("rejects non-PDF and oversized inputs before network access", async () => {
    const fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
    const image = new File(["image"], "page.png", { type: "image/png" });
    const oversized = new File(["%PDF-1.7\n"], "huge.pdf", { type: "application/pdf" });
    Object.defineProperty(oversized, "size", { value: 256 * 1024 * 1024 + 1 });
    const signal = new AbortController().signal;

    await expect(uploadPdfImport(image, "en", signal)).rejects.toEqual(new ApiError("pdf_invalid"));
    await expect(uploadPdfImport(oversized, "en", signal)).rejects.toEqual(new ApiError("pdf_invalid"));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
