import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type DocumentProgress,
  type DocumentSnapshot,
  type DocumentUploadData,
  type StudyPage,
} from "../../lib/api";
import type { DictionaryLanguage } from "../../lib/dictionaryLanguage";
import type { StudyLanguage } from "../../lib/studyLanguage";
import type { UiLocale } from "../../lib/uiLocale";
import { DocumentReader } from "./DocumentReader";

const apiMocks = vi.hoisted(() => ({
  cancelDocumentProcessing: vi.fn(),
  fetchDocumentPage: vi.fn(),
  fetchDocumentProtectedImage: vi.fn(),
  fetchDocumentSnapshot: vi.fn(),
  reorderDocument: vi.fn(),
  reprocessDocumentDictionaryLanguage: vi.fn(),
  reprocessDocumentStudyLanguage: vi.fn(),
  retryFailedDocumentPages: vi.fn(),
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
  ReaderWorkspace: ({
    page,
    languageMutation,
    studyLanguageError,
    dictionaryLanguageError,
    onStudyLanguageChange,
    onDictionaryLanguageChange,
    onReset,
  }: {
    page: StudyPage;
    languageMutation: "study" | "dictionary" | null;
    studyLanguageError: string | null;
    dictionaryLanguageError: string | null;
    onStudyLanguageChange: (language: StudyLanguage) => void;
    onDictionaryLanguageChange: (language: DictionaryLanguage) => void;
    onReset: () => void;
  }) => (
    <div>
      <div data-testid="reader-page">{page.pageId}</div>
      <div data-testid="reader-study">{page.studyLanguage}</div>
      <div data-testid="reader-dictionary">{page.requestedDictionaryLanguage ?? "en"}</div>
      <div data-testid="reader-mutation">{languageMutation ?? "none"}</div>
      {studyLanguageError ? <div data-testid="study-error">{studyLanguageError}</div> : null}
      {dictionaryLanguageError ? <div data-testid="dictionary-error">{dictionaryLanguageError}</div> : null}
      <button type="button" onClick={() => onStudyLanguageChange("en")}>change-study</button>
      <button type="button" onClick={() => onDictionaryLanguageChange("de")}>change-dictionary</button>
      <button type="button" onClick={onReset}>reset-reader</button>
    </div>
  ),
}));

const capabilities = {
  readDocument: "read-document",
  readDocumentImage: "read-image",
  reprocessDocument: "reprocess-document",
  manageDocument: "manage-document",
};

type LegacyProgressInput = Omit<DocumentProgress, "cancelledPages"> & {
  readonly cancelledPages?: number;
};

function aggregateStatus(progress: DocumentProgress): DocumentSnapshot["status"] {
  if (progress.processingPages > 0) return "processing";
  if (progress.cancelledPages > 0) return "cancelled";
  if (progress.failedPages > 0) return "completed_with_errors";
  return "completed";
}

function access(
  pages: DocumentUploadData["pages"],
  progressInput: LegacyProgressInput,
): DocumentUploadData {
  const progress: DocumentProgress = {
    ...progressInput,
    cancelledPages: progressInput.cancelledPages ?? 0,
  };
  return {
    documentId: "document-1",
    sourceKind: "images",
    expiresAt: "2026-08-11T12:00:00Z",
    orderRevision: 1,
    status: aggregateStatus(progress),
    pages,
    progress,
    capabilities,
  };
}

function studyPage(pageId: string, overrides: Partial<StudyPage> = {}): StudyPage {
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
    ...overrides,
  };
}

interface RenderOptions {
  readonly uiLocale?: UiLocale;
  readonly preferredStudyLanguage?: StudyLanguage;
  readonly preferredDictionaryLanguage?: DictionaryLanguage;
  readonly onPreferredStudyLanguageChange?: (language: StudyLanguage) => void;
  readonly onPreferredDictionaryLanguageChange?: (language: DictionaryLanguage) => void;
  readonly onReset?: () => void;
}

function renderReader(document: DocumentUploadData, options: RenderOptions = {}) {
  return render(
    <DocumentReader
      access={document}
      uiLocale={options.uiLocale ?? "en"}
      preferredStudyLanguage={options.preferredStudyLanguage ?? "pt-BR"}
      preferredDictionaryLanguage={options.preferredDictionaryLanguage ?? "en"}
      onPreferredStudyLanguageChange={options.onPreferredStudyLanguageChange ?? vi.fn()}
      onPreferredDictionaryLanguageChange={options.onPreferredDictionaryLanguageChange ?? vi.fn()}
      onReset={options.onReset ?? vi.fn()}
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
  apiMocks.retryFailedDocumentPages.mockResolvedValue({
    created: true,
    retriedPageIds: ["page-b"],
    jobIds: ["retry-job"],
    status: "processing",
    progress: {
      totalPages: 2,
      completedPages: 1,
      processingPages: 1,
      failedPages: 0,
      cancelledPages: 0,
    },
  });
  apiMocks.cancelDocumentProcessing.mockResolvedValue({
    cancelledPages: 1,
    cancelRequestedPages: 0,
    status: "cancelled",
    progress: {
      totalPages: 2,
      completedPages: 1,
      processingPages: 0,
      failedPages: 0,
      cancelledPages: 1,
    },
  });
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
    expect(
      screen.getByText("1 / 2 pages readable · 1 processing · 0 failed · 0 cancelled"),
    ).toBeVisible();
    expect(screen.getByText("Document processing")).toBeVisible();
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

  it("makes the selected processing page readable after an aggregate refresh", async () => {
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "processing_ocr", resultAvailable: false }],
      { totalPages: 1, completedPages: 0, processingPages: 1, failedPages: 0 },
    );
    const completed: DocumentSnapshot = {
      ...document,
      status: "completed",
      pages: [{ pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true }],
      progress: {
        totalPages: 1,
        completedPages: 1,
        processingPages: 0,
        failedPages: 0,
        cancelledPages: 0,
      },
    };
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(completed);

    renderReader(document);
    expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();
    await waitFor(
      () => expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(1),
      { timeout: 1_500 },
    );

    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);
  });

  it("continues aggregate polling while processing remains and stops on terminal progress", async () => {
    vi.useFakeTimers();
    const pages = [
      { pageId: "page-a", ordinal: 0, status: "pending" as const, resultAvailable: false },
    ];
    const document = access(pages, {
      totalPages: 1,
      completedPages: 0,
      processingPages: 1,
      failedPages: 0,
    });
    const stillProcessing: DocumentSnapshot = {
      ...document,
      status: "processing",
      pages: [{ ...pages[0]!, status: "processing_linguistics" }],
    };
    const terminal: DocumentSnapshot = {
      ...document,
      status: "completed_with_errors",
      pages: [{ ...pages[0]!, status: "failed" }],
      progress: {
        totalPages: 1,
        completedPages: 0,
        processingPages: 0,
        failedPages: 1,
        cancelledPages: 0,
      },
    };
    apiMocks.fetchDocumentSnapshot
      .mockResolvedValueOnce(stillProcessing)
      .mockResolvedValueOnce(terminal);

    renderReader(document);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });
    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(2);
    expect(
      screen.getByText("0 / 1 pages readable · 0 processing · 1 failed · 0 cancelled"),
    ).toBeVisible();
    expect(screen.getByText("Document complete with errors")).toBeVisible();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(2);
  });

  it("recovers aggregate polling after a transient request error", async () => {
    vi.useFakeTimers();
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "pending", resultAvailable: false }],
      { totalPages: 1, completedPages: 0, processingPages: 1, failedPages: 0 },
    );
    const terminal: DocumentSnapshot = {
      ...document,
      status: "completed_with_errors",
      pages: [{ pageId: "page-a", ordinal: 0, status: "failed", resultAvailable: false }],
      progress: {
        totalPages: 1,
        completedPages: 0,
        processingPages: 0,
        failedPages: 1,
        cancelledPages: 0,
      },
    };
    apiMocks.fetchDocumentSnapshot
      .mockRejectedValueOnce(new Error("temporary network failure"))
      .mockResolvedValueOnce(terminal);

    renderReader(document);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(700);
    });
    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(apiMocks.fetchDocumentSnapshot).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("alert")).toHaveTextContent("Failed");
  });

  it.each(["failed", "expired"] as const)(
    "renders %s children as failed without fetching a result",
    (status) => {
      const document = access(
        [{ pageId: "page-a", ordinal: 0, status, resultAvailable: false }],
        { totalPages: 1, completedPages: 0, processingPages: 0, failedPages: 1 },
      );

      renderReader(document);

      expect(screen.getByRole("alert")).toHaveTextContent("Failed");
      expect(
        screen.getByRole("button", { name: new RegExp(`Page 1: ${status}`) }),
      ).toHaveAttribute("data-page-status", "failed");
      expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();
    },
  );

  it("renders cancelled children as terminal without fetching a result", () => {
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "cancelled", resultAvailable: false }],
      {
        totalPages: 1,
        completedPages: 0,
        processingPages: 0,
        failedPages: 0,
        cancelledPages: 1,
      },
    );

    renderReader(document);

    expect(screen.getByRole("alert")).toHaveTextContent("Cancelled");
    expect(screen.getByText("Document processing cancelled")).toBeVisible();
    expect(screen.getByRole("button", { name: "Page 1: cancelled" })).toHaveAttribute(
      "data-page-status",
      "cancelled",
    );
    expect(apiMocks.fetchDocumentPage).not.toHaveBeenCalled();
  });

  it("retries failed children without recomputing the readable sibling", async () => {
    const user = userEvent.setup();
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-b", ordinal: 1, status: "failed", resultAvailable: false },
      ],
      { totalPages: 2, completedPages: 1, processingPages: 0, failedPages: 1 },
    );
    const processing: DocumentSnapshot = {
      ...document,
      status: "processing",
      pages: [
        document.pages[0]!,
        { pageId: "page-b", ordinal: 1, status: "pending", resultAvailable: false },
      ],
      progress: {
        totalPages: 2,
        completedPages: 1,
        processingPages: 1,
        failedPages: 0,
        cancelledPages: 0,
      },
    };
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(processing);

    renderReader(document);
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Retry failed pages" }));

    await waitFor(() => expect(apiMocks.retryFailedDocumentPages).toHaveBeenCalledTimes(1));
    expect(apiMocks.retryFailedDocumentPages).toHaveBeenCalledWith(
      document,
      expect.any(AbortSignal),
    );
    expect(await screen.findByText("Document processing")).toBeVisible();
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Page 1: readable" })).toHaveAttribute(
      "data-page-status",
      "readable",
    );
  });

  it("cancels unfinished server work while keeping a completed sibling readable", async () => {
    const user = userEvent.setup();
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-b", ordinal: 1, status: "pending", resultAvailable: false },
      ],
      { totalPages: 2, completedPages: 1, processingPages: 1, failedPages: 0 },
    );
    const cancelled: DocumentSnapshot = {
      ...document,
      status: "cancelled",
      pages: [
        document.pages[0]!,
        { pageId: "page-b", ordinal: 1, status: "cancelled", resultAvailable: false },
      ],
      progress: {
        totalPages: 2,
        completedPages: 1,
        processingPages: 0,
        failedPages: 0,
        cancelledPages: 1,
      },
    };
    apiMocks.fetchDocumentSnapshot.mockResolvedValue(cancelled);

    renderReader(document);
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");

    await user.click(screen.getByRole("button", { name: "Cancel processing" }));

    await waitFor(() => expect(apiMocks.cancelDocumentProcessing).toHaveBeenCalledTimes(1));
    expect(apiMocks.cancelDocumentProcessing).toHaveBeenCalledWith(
      document,
      expect.any(AbortSignal),
    );
    expect(screen.getByText("Document processing cancelled")).toBeVisible();
    expect(screen.getByTestId("reader-page")).toHaveTextContent("page-a");
    expect(screen.getByRole("button", { name: "Page 2: cancelled" })).toHaveAttribute(
      "data-page-status",
      "cancelled",
    );
  });

  it("persists current-page reorder through the versioned backend contract", async () => {
    const user = userEvent.setup();
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-b", ordinal: 1, status: "completed", resultAvailable: true },
        { pageId: "page-c", ordinal: 2, status: "completed", resultAvailable: true },
      ],
      { totalPages: 3, completedPages: 3, processingPages: 0, failedPages: 0 },
    );
    const reordered: DocumentSnapshot = {
      ...document,
      orderRevision: 2,
      pages: [
        { pageId: "page-b", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-a", ordinal: 1, status: "completed", resultAvailable: true },
        { pageId: "page-c", ordinal: 2, status: "completed", resultAvailable: true },
      ],
    };
    apiMocks.reorderDocument.mockResolvedValue(reordered);

    renderReader(document);
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");

    await user.click(screen.getByRole("button", { name: "Move page later" }));

    await waitFor(() => expect(apiMocks.reorderDocument).toHaveBeenCalledTimes(1));
    expect(apiMocks.reorderDocument).toHaveBeenCalledWith(
      document,
      ["page-b", "page-a", "page-c"],
      1,
      expect.any(AbortSignal),
    );
    expect(screen.getByText("Page 2 of 3")).toBeVisible();
    expect(screen.getByRole("button", { name: "Page 2: readable" })).toHaveAttribute(
      "aria-current",
      "page",
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
    expect(screen.getByRole("button", { name: "Previous" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-b");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:page-a");
    expect(screen.getByText("Page 2 of 2")).toBeVisible();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Previous" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");
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

  it("revokes an image that resolves before its StudyPage request fails", async () => {
    let rejectPage: ((reason: unknown) => void) | undefined;
    apiMocks.fetchDocumentPage.mockImplementation(
      () => new Promise<StudyPage>((_resolve, reject) => {
        rejectPage = reject;
      }),
    );
    apiMocks.fetchDocumentProtectedImage.mockResolvedValue("blob:orphaned-image");
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true }],
      { totalPages: 1, completedPages: 1, processingPages: 0, failedPages: 0 },
    );

    renderReader(document);
    await waitFor(() => expect(apiMocks.fetchDocumentProtectedImage).toHaveBeenCalledTimes(1));
    await act(async () => {
      rejectPage?.(new ApiError("not_found"));
    });

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:orphaned-image");
  });

  it("revokes the current Blob URL when the reader unmounts", async () => {
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true }],
      { totalPages: 1, completedPages: 1, processingPages: 0, failedPages: 0 },
    );
    const rendered = renderReader(document);
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");

    rendered.unmount();

    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:page-a");
  });

  it("keeps historical dictionary metadata readable without automatic reprojection", async () => {
    apiMocks.fetchDocumentPage.mockResolvedValue(
      studyPage("page-a", { requestedDictionaryLanguage: "de" }),
    );
    const document = access(
      [
        { pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true },
        { pageId: "page-b", ordinal: 1, status: "completed", resultAvailable: true },
      ],
      { totalPages: 2, completedPages: 2, processingPages: 0, failedPages: 0 },
    );

    renderReader(document, { preferredDictionaryLanguage: "en" });

    expect(await screen.findByTestId("reader-dictionary")).toHaveTextContent("de");
    expect(apiMocks.reprocessDocumentDictionaryLanguage).not.toHaveBeenCalled();
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);
  });

  it("reprocesses study language only for the current child", async () => {
    const user = userEvent.setup();
    const onPreferredStudyLanguageChange = vi.fn();
    pollingMocks.waitForDocumentPageResult.mockResolvedValue(
      studyPage("page-a", { studyLanguage: "en" }),
    );
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true }],
      { totalPages: 1, completedPages: 1, processingPages: 0, failedPages: 0 },
    );
    renderReader(document, { onPreferredStudyLanguageChange });
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "change-study" }));

    await waitFor(() => {
      expect(apiMocks.reprocessDocumentStudyLanguage).toHaveBeenCalledWith(
        document,
        "page-a",
        "en",
        expect.any(AbortSignal),
      );
    });
    expect(onPreferredStudyLanguageChange).toHaveBeenLastCalledWith("en");
    expect(await screen.findByTestId("reader-study")).toHaveTextContent("en");
  });

  it("ignores retired dictionary mutation callbacks for the current child", async () => {
    const user = userEvent.setup();
    const onPreferredDictionaryLanguageChange = vi.fn();
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true }],
      { totalPages: 1, completedPages: 1, processingPages: 0, failedPages: 0 },
    );
    renderReader(document, { onPreferredDictionaryLanguageChange });
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "change-dictionary" }));

    expect(apiMocks.reprocessDocumentDictionaryLanguage).not.toHaveBeenCalled();
    expect(onPreferredDictionaryLanguageChange).not.toHaveBeenCalled();
    expect(screen.getByTestId("reader-dictionary")).toHaveTextContent("en");
    expect(screen.getByTestId("reader-mutation")).toHaveTextContent("none");
  });

  it("does not surface dictionary mutation errors after the retired action is ignored", async () => {
    const user = userEvent.setup();
    apiMocks.reprocessDocumentDictionaryLanguage.mockRejectedValue(new Error("projection failed"));
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "completed", resultAvailable: true }],
      { totalPages: 1, completedPages: 1, processingPages: 0, failedPages: 0 },
    );
    renderReader(document);
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "change-dictionary" }));

    expect(apiMocks.reprocessDocumentDictionaryLanguage).not.toHaveBeenCalled();
    expect(screen.queryByTestId("dictionary-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("reader-dictionary")).toHaveTextContent("en");
  });

  it("renders localized Portuguese document navigation without changing page semantics", () => {
    const document = access(
      [{ pageId: "page-a", ordinal: 0, status: "processing_ocr", resultAvailable: false }],
      { totalPages: 1, completedPages: 0, processingPages: 1, failedPages: 0 },
    );

    renderReader(document, { uiLocale: "pt-BR" });

    expect(screen.getByText("Página 1 de 1")).toBeVisible();
    expect(screen.getAllByText("Processando")).toHaveLength(2);
    expect(screen.getByText("Documento em processamento")).toBeVisible();
    expect(screen.getByRole("button", { name: "Página 1: processing ocr" })).toHaveAttribute(
      "data-page-status",
      "processing",
    );
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
      status: "completed_with_errors",
      pages: pages.map((item) => ({ ...item, status: "failed" as const })),
      progress: {
        totalPages: 200,
        completedPages: 0,
        processingPages: 0,
        failedPages: 200,
        cancelledPages: 0,
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
    expect(
      screen.getByText("0 / 200 pages readable · 0 processing · 200 failed · 0 cancelled"),
    ).toBeVisible();
  });
});
