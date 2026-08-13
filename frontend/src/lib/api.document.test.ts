import { afterEach, describe, expect, it, vi } from "vitest";

import {
  type DocumentUploadData,
  fetchDocumentPage,
  fetchDocumentProtectedImage,
  fetchDocumentSnapshot,
  reprocessDocumentStudyLanguage,
  uploadDocument,
} from "./api";

const access: DocumentUploadData = {
  documentId: "document-001",
  sourceKind: "images",
  expiresAt: "2026-08-11T12:00:00Z",
  orderRevision: 1,
  status: "processing",
  pages: [
    { pageId: "page-a", ordinal: 0, status: "pending", resultAvailable: false },
    { pageId: "page-b", ordinal: 1, status: "pending", resultAvailable: false },
  ],
  progress: {
    totalPages: 2,
    completedPages: 0,
    processingPages: 2,
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

function envelope(data: unknown, status = 200): Response {
  return Response.json({ success: true, data, error: null }, { status });
}

describe("document API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("sends ordered images and one study language without dictionary parameters", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => envelope(access, 202));
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000010"),
      getRandomValues: vi.fn(),
    });
    const first = new File(["first"], "z-last.png", { type: "image/png" });
    const second = new File(["second"], "a-first.png", { type: "image/png" });

    await expect(
      uploadDocument([first, second], "en", new AbortController().signal),
    ).resolves.toEqual(access);

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/documents");
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.headers).toEqual({
      "Idempotency-Key": "document-upload-00000000-0000-4000-8000-000000000010",
    });
    const form = init.body as FormData;
    expect(form.getAll("images[]")).toEqual([first, second]);
    expect(form.getAll("studyLanguage")).toEqual(["en"]);
    expect(form.has("dictionaryLanguage")).toBe(false);
  });

  it("preserves duplicate logical files in multipart order", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => envelope(access, 202)));
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000011"),
      getRandomValues: vi.fn(),
    });
    const duplicate = new File(["same"], "duplicate.png", { type: "image/png" });

    await uploadDocument([duplicate, duplicate], "pt-BR", new AbortController().signal);

    const fetchMock = vi.mocked(fetch);
    const form = fetchMock.mock.calls[0]?.[1]?.body as FormData;
    expect(form.getAll("images[]")).toEqual([duplicate, duplicate]);
  });

  it("uses readDocument for aggregate and child StudyPage reads", async () => {
    const studyPage = {
      pageId: "page-a",
      status: "completed",
      resultAvailable: true,
      contentLanguage: "ja",
      studyLanguage: "pt-BR",
      dictionaryLanguage: "en",
      expiresAt: access.expiresAt,
      imageUrl: "/image",
      dimensions: { width: 80, height: 120 },
      regions: [],
      error: null,
      ocr: { detector: "default", recognizer: "48px", upstreamCommit: "commit" },
    } as const;
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(envelope(access))
      .mockResolvedValueOnce(envelope(studyPage));
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;

    await fetchDocumentSnapshot(access, signal);
    await fetchDocumentPage(access, "page-a", signal);

    expect(fetchMock.mock.calls[0]?.[1]?.headers).toEqual({
      "X-Document-Token": "read-document-token",
    });
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "/api/v1/documents/document-001/pages/page-a",
    );
    expect(fetchMock.mock.calls[1]?.[1]?.headers).toEqual({
      "X-Document-Token": "read-document-token",
    });
  });

  it("uses only readDocumentImage for the selected child image", async () => {
    const createObjectURL = vi.fn(() => "blob:document-page");
    vi.stubGlobal("URL", { ...URL, createObjectURL });
    const fetchMock = vi.fn<typeof fetch>(async () => new Response("image"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      fetchDocumentProtectedImage(access, "page-b", new AbortController().signal),
    ).resolves.toBe("blob:document-page");

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/api/v1/documents/document-001/pages/page-b/image",
    );
    expect(fetchMock.mock.calls[0]?.[1]?.headers).toEqual({
      "X-Document-Token": "read-document-image-token",
    });
  });

  it("uses reprocessDocument only for the supported current-child study mutation", async () => {
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn().mockReturnValue("study-key"),
      getRandomValues: vi.fn(),
    });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      envelope(
        { jobId: "study-job", status: "pending", studyLanguage: "en", created: true },
        202,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;

    await reprocessDocumentStudyLanguage(access, "page-b", "en", signal);

    expect(fetchMock).toHaveBeenCalledOnce();
    const study = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(study.headers).toEqual({
      "Content-Type": "application/json",
      "Idempotency-Key": "document-study-reprocess-study-key",
      "X-Document-Token": "reprocess-document-token",
    });
    expect(study.body).toBe(JSON.stringify({ studyLanguage: "en" }));
    expect(String(study.body)).not.toContain("dictionaryLanguage");
  });
});
