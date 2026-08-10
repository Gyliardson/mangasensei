import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type {
  DocumentSnapshot,
  DocumentUploadData,
  StudyPage,
} from "../../lib/api";
import { DocumentReader } from "./DocumentReader";

const apiMocks = vi.hoisted(() => ({
  fetchDocumentPage: vi.fn(),
  fetchDocumentProtectedImage: vi.fn(),
  fetchDocumentSnapshot: vi.fn(),
  reprocessDocumentDictionaryLanguage: vi.fn(),
  reprocessDocumentStudyLanguage: vi.fn(),
}));
const pollingMocks = vi.hoisted(() => ({
  waitForDocumentPageResult: vi.fn(),
}));

vi.mock("../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/api")>();
  return { ...actual, ...apiMocks };
});

vi.mock("../../lib/documentPolling", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../lib/documentPolling")>();
  return { ...actual, ...pollingMocks };
});

vi.mock("./ReaderWorkspace", () => ({
  ReaderWorkspace: ({ page }: { page: StudyPage }) => (
    <div data-testid="reader-page">{page.pageId}</div>
  ),
}));

const capabilities = {
  readDocument: "read-document",
  readDocumentImage: "read-image",
  reprocessDocument: "reprocess-document",
};

function access(
  pages: DocumentUploadData["pages"],
  progress: DocumentUploadData["progress"],
): DocumentUploadData {
  return {
    documentId: "document-1",
    sourceKind: "images",
    expiresAt: "2026-08-11T12:00:00Z",
    orderRevision: 1,
    pages,
    progress,
    capabilities,
  };
}

function studyPage(pageId: string): StudyPage {
  return {
    pageId,
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage: "pt-BR",
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: "en",
    expiresAt: "2026-08-11T12:00:00Z",
    imageUrl: `/image/${pageId}`,
    dimensions: { width: 80, height: 120 },
    regions: [],
    error: null,
    ocr: { detector: "default", recognizer: "48px", upstreamCommit: "commit" },
  };
}

function renderReader(document: DocumentUploadData) {
  return render(
    <DocumentReader
      access={document}
      uiLocale="en"
      preferredStudyLanguage="pt-BR"
      preferredDictionaryLanguage="en"
      onPreferredStudyLanguageChange={vi.fn()}
      onPreferredDictionaryLanguageChange={vi.fn()}
      onReset={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.fetchDocumentPage.mockImplementation(async (_access, pageId: string) =>
    studyPage(pageId),
  );
  apiMocks.fetchDocumentProtectedImage.mockImplementation(async (_access, pageId: string) =>
    `blob:${pageId}`,
  );
  apiMocks.fetchDocumentSnapshot.mockImplementation(async (document: DocumentUploadData) =>
    document,
  );
  pollingMocks.waitForDocumentPageResult.mockImplementation(
    async (_access, pageId: string) => studyPage(pageId),
  );
  vi.stubGlobal("URL", { ...URL, revokeObjectURL: vi.fn() });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("DocumentReader", () => {
  it("opens a completed child while a sibling is still processing", async () => {
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-b", ordinal: 1, status: "processing_ocr", resultAvailable: false },
      ],
      { totalPages: 2, completedPages: 1, processingPages: 1, failedPages: 0 },
    );

    renderReader(document);

    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");
    expect(screen.getByText("1 / 2 pages complete · 1 processing · 0 failed")).toBeVisible();
    const pageButtons = screen.getAllByRole("button", { name: /^Page / });
    expect(pageButtons[0]).toHaveAttribute("aria-current", "page");
    expect(pageButtons[0]).toHaveAttribute("data-page-status", "readable");
    expect(pageButtons[1]).toHaveAttribute("data-page-status", "processing");
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);
  });

  it("does not fetch StudyPage for an unreadable current child and can select a readable sibling", async () => {
    const user = userEvent.setup();
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "processing_ocr", resultAvailable: false },
        { pageId: "page-b", ordinal: 1, status: "completed", resultAvailable: true },
      ],
      { totalPages: 2, completedPages: 1, processingPages: 1, failedPages: 0 },
    );
    renderReader(document);

    expect(screen.getAllByText("Processing")).toHaveLength(2);
    expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();

    const pageButtons = screen.getAllByRole("button", { name: /^Page / });
    await user.click(pageButtons[1]!);

    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-b");
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchDocumentPage).toHaveBeenLastCalledWith(
      document,
      "page-b",
      expect.any(AbortSignal),
    );
  });

  it("revokes the old Blob URL when navigating to another completed child", async () => {
    const user = userEvent.setup();
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-b", ordinal: 1, status: "completed", resultAvailable: true },
      ],
      { totalPages: 2, completedPages: 2, processingPages: 0, failedPages: 0 },
    );
    renderReader(document);
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-b");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:page-a");
    expect(screen.getByText("Page 2 of 2")).toBeVisible();
  });

  it("ignores a late response from the previously selected page", async () => {
    const user = userEvent.setup();
    let resolvePageA: ((page: StudyPage) => void) | undefined;
    apiMocks.fetchDocumentPage.mockImplementation(async (_access, pageId: string) => {
      if (pageId === "page-a") {
        return new Promise<StudyPage>((resolve) => {
          resolvePageA = resolve;
        });
      }
      return studyPage("page-b");
    });
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-b", ordinal: 1, status: "completed", resultAvailable: true },
      ],
      { totalPages: 2, completedPages: 2, processingPages: 0, failedPages: 0 },
    );
    renderReader(document);

    const pageButtons = screen.getAllByRole("button", { name: /^Page / });
    await user.click(pageButtons[1]!);
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-b");

    await act(async () => resolvePageA?.(studyPage("page-a")));

    expect(screen.getByTestId("reader-page")).toHaveTextContent("page-b");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:page-a");
  });

  it("renders 200 compact statuses while issuing one aggregate request per polling tick", async () => {
    vi.useFakeTimers();
    const pages = Array.from({ length: 200 }, (_, ordinal) => ({
      pageId: `page-${ordinal}`,
      ordinal,
      status: "pending" as const,
      resultAvailable: false,
    }));
    const document = access(pages, {
      totalPages: 200,
      completedPages: 0,
      processingPages: 200,
      failedPages: 0,
    });
    const terminal: DocumentSnapshot = {
      ...document,
      pages: pages.map((item) => ({ ...item, status: "failed" as const })),
      progress: {
        totalPages: 200,
        completedPages: 0,
        processingPages: 0,
        failedPages: 200,
      },
    };
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(terminal);

    renderReader(document);
    expect(screen.getAllByRole("button", { name: /^Page / })).toHaveLength(200);
    expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(750);
    });

    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();
    expect(apiMocks.fetchDocumentProtectedImage).not.toHaveBeenCalled();
    expect(screen.getByText("0 / 200 pages complete · 0 processing · 200 failed")).toBeVisible();
  });
});
