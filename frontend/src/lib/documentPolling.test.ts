import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  type DocumentSnapshot,
  type DocumentUploadData,
  type JobStatus,
  type StudyPage,
} from "./api";
import { documentNeedsPolling, waitForDocumentPageResult } from "./documentPolling";

const apiMocks = vi.hoisted(() => ({
  fetchDocumentPage: vi.fn(),
  fetchDocumentSnapshot: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, ...apiMocks };
});

const access: DocumentUploadData = {
  documentId: "document-1",
  sourceKind: "images",
  expiresAt: "2026-08-11T12:00:00Z",
  orderRevision: 1,
  status: "processing",
  pages: [],
  progress: {
    totalPages: 1,
    completedPages: 0,
    processingPages: 1,
    failedPages: 0,
    cancelledPages: 0,
  },
  capabilities: {
    readDocument: "read-document",
    readDocumentImage: "read-image",
    reprocessDocument: "reprocess-document",
    manageDocument: "manage-document",
  },
};

function aggregateStatus(
  status: JobStatus,
  resultAvailable: boolean,
): DocumentSnapshot["status"] {
  if (
    status === "pending" ||
    status === "claimed" ||
    status === "processing_ocr" ||
    status === "processing_linguistics" ||
    status === "processing_gemini" ||
    status === "retryable_failure"
  ) {
    return "processing";
  }
  if (resultAvailable) return "completed";
  if (status === "cancelled") return "cancelled";
  if (status === "failed" || status === "expired") return "completed_with_errors";
  return "completed";
}

function snapshot(
  status: JobStatus,
  resultAvailable: boolean,
  processingPages = status === "completed" || status === "failed" || status === "expired" || status === "cancelled"
    ? 0
    : 1,
): DocumentSnapshot {
  return {
    ...access,
    status: aggregateStatus(status, resultAvailable),
    pages: [{ pageId: "page-1", ordinal: 0, status, resultAvailable }],
    progress: {
      totalPages: 1,
      completedPages: resultAvailable ? 1 : 0,
      processingPages,
      failedPages: status === "failed" || status === "expired" ? 1 : 0,
      cancelledPages: status === "cancelled" ? 1 : 0,
    },
  };
}

function studyPage(overrides: Partial<StudyPage> = {}): StudyPage {
  return {
    pageId: "page-1",
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage: "pt-BR",
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: "en",
    expiresAt: "2026-08-11T12:00:00Z",
    imageUrl: "/image/page-1",
    dimensions: { width: 80, height: 120 },
    regions: [],
    error: null,
    ocr: { detector: "default", recognizer: "48px", upstreamCommit: "commit" },
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("document polling", () => {
  it("uses aggregate status as the polling authority", () => {
    expect(documentNeedsPolling(snapshot("pending", false, 1))).toBe(true);
    expect(documentNeedsPolling(snapshot("pending", false, 0))).toBe(true);
    expect(documentNeedsPolling(snapshot("failed", false, 0))).toBe(false);
    expect(documentNeedsPolling(snapshot("cancelled", false, 0))).toBe(false);
  });

  it("returns a completed child after one aggregate refresh", async () => {
    const completed = snapshot("completed", true);
    const page = studyPage();
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(completed);
    apiMocks.fetchDocumentPage.mockResolvedValue(page);
    const onSnapshot = vi.fn();

    await expect(
      waitForDocumentPageResult(
        access,
        "page-1",
        new AbortController().signal,
        onSnapshot,
        (candidate) => candidate.studyLanguage === "pt-BR",
      ),
    ).resolves.toEqual(page);
    expect(onSnapshot).toHaveBeenCalledWith(completed);
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);
  });

  it("waits through processing and then returns the completed child", async () => {
    vi.useFakeTimers();
    const completed = snapshot("completed", true);
    const page = studyPage({ studyLanguage: "en" });
    apiMocks.fetchDocumentSnapshot
      .mockResolvedValueOnce(snapshot("processing_ocr", false))
      .mockResolvedValueOnce(completed);
    apiMocks.fetchDocumentPage.mockResolvedValue(page);

    const result = waitForDocumentPageResult(
      access,
      "page-1",
      new AbortController().signal,
      vi.fn(),
      (candidate) => candidate.studyLanguage === "en",
    );
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(600);

    await expect(result).resolves.toEqual(page);
    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(2);
  });

  it("rejects completed results that do not satisfy the mutation predicate", async () => {
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(snapshot("completed", true));
    apiMocks.fetchDocumentPage.mockResolvedValue(studyPage());

    await expect(
      waitForDocumentPageResult(
        access,
        "page-1",
        new AbortController().signal,
        vi.fn(),
        () => false,
      ),
    ).rejects.toMatchObject({ code: "request_failed" });
  });

  it("surfaces terminal failure with and without a prior readable result", async () => {
    apiMocks.fetchDocumentSnapshot.mockResolvedValueOnce(snapshot("failed", false));
    await expect(
      waitForDocumentPageResult(
        access,
        "page-1",
        new AbortController().signal,
        vi.fn(),
        () => true,
      ),
    ).rejects.toMatchObject({ code: "failed" });
    expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();

    apiMocks.fetchDocumentSnapshot.mockResolvedValueOnce(snapshot("failed", true));
    apiMocks.fetchDocumentPage.mockResolvedValueOnce(
      studyPage({ error: { code: "pipeline_failed", message: "failed" } }),
    );
    await expect(
      waitForDocumentPageResult(
        access,
        "page-1",
        new AbortController().signal,
        vi.fn(),
        () => true,
      ),
    ).rejects.toMatchObject({ code: "pipeline_failed" });
  });

  it("surfaces terminal cancellation without fetching an unreadable result", async () => {
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(snapshot("cancelled", false));

    await expect(
      waitForDocumentPageResult(
        access,
        "page-1",
        new AbortController().signal,
        vi.fn(),
        () => true,
      ),
    ).rejects.toMatchObject({ code: "cancelled" });
    expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();
  });

  it("falls back to the terminal status when a readable failed result has no error code", async () => {
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(snapshot("expired", true));
    apiMocks.fetchDocumentPage.mockResolvedValue(studyPage());

    await expect(
      waitForDocumentPageResult(
        access,
        "page-1",
        new AbortController().signal,
        vi.fn(),
        () => true,
      ),
    ).rejects.toMatchObject({ code: "expired" });
  });

  it("rejects when the requested child disappears from the aggregate", async () => {
    apiMocks.fetchDocumentSnapshot.mockResolvedValue({
      ...access,
      status: "completed",
      pages: [],
      progress: {
        totalPages: 0,
        completedPages: 0,
        processingPages: 0,
        failedPages: 0,
        cancelledPages: 0,
      },
    });

    await expect(
      waitForDocumentPageResult(
        access,
        "page-1",
        new AbortController().signal,
        vi.fn(),
        () => true,
      ),
    ).rejects.toMatchObject({ code: "not_found" });
  });

  it("aborts an in-flight backoff without another aggregate request", async () => {
    vi.useFakeTimers();
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(snapshot("processing_linguistics", false));
    const controller = new AbortController();

    const result = waitForDocumentPageResult(
      access,
      "page-1",
      controller.signal,
      vi.fn(),
      () => true,
    );
    await Promise.resolve();
    controller.abort();

    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(1);
  });

  it("rejects immediately when already aborted", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(
      waitForDocumentPageResult(access, "page-1", controller.signal, vi.fn(), () => true),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(apiMocks.fetchDocumentSnapshot).not.toHaveBeenCalled();
  });
});
