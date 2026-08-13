import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type DocumentUploadData,
  type StudyPage,
} from "../../lib/api";
import type { StudyLanguage } from "../../lib/studyLanguage";
import { DocumentReader } from "./DocumentReader";

const apiMocks = vi.hoisted(() => ({
  fetchDocumentPage: vi.fn(),
  fetchDocumentProtectedImage: vi.fn(),
  fetchDocumentSnapshot: vi.fn(),
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
  ReaderWorkspace: ({
    page,
    languageMutation,
    studyLanguageError,
    onStudyLanguageChange,
  }: {
    page: StudyPage;
    languageMutation: "study" | null;
    studyLanguageError: string | null;
    onStudyLanguageChange: (language: StudyLanguage) => void;
  }) => (
    <div>
      <div data-testid="reader-page">{page.pageId}</div>
      <div data-testid="dictionary-requested">{page.requestedDictionaryLanguage ?? "en"}</div>
      <div data-testid="mutation">{languageMutation ?? "none"}</div>
      {studyLanguageError ? <div data-testid="study-error">{studyLanguageError}</div> : null}
      <button type="button" onClick={() => onStudyLanguageChange("en")}>study-en</button>
    </div>
  ),
}));

const capabilities = {
  readDocument: "read-document",
  readDocumentImage: "read-image",
  reprocessDocument: "reprocess-document",
  manageDocument: "manage-document",
};

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

function documentAccess(pageIds: string[] = ["page-a"]): DocumentUploadData {
  return {
    documentId: "document-1",
    sourceKind: "images",
    expiresAt: "2026-08-11T12:00:00Z",
    orderRevision: 1,
    status: "completed",
    pages: pageIds.map((pageId, ordinal) => ({
      pageId,
      ordinal,
      status: "completed" as const,
      resultAvailable: true,
    })),
    progress: {
      totalPages: pageIds.length,
      completedPages: pageIds.length,
      processingPages: 0,
      failedPages: 0,
      cancelledPages: 0,
    },
    capabilities,
  };
}

function renderReader(
  access: DocumentUploadData,
  options: {
    preferredStudyLanguage?: StudyLanguage;
    onStudy?: (language: StudyLanguage) => void;
  } = {},
) {
  return render(
    <DocumentReader
      access={access}
      uiLocale="en"
      preferredStudyLanguage={options.preferredStudyLanguage ?? "pt-BR"}
      onPreferredStudyLanguageChange={options.onStudy ?? vi.fn()}
      onReset={vi.fn()}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.fetchDocumentPage.mockImplementation(async (_access, pageId: string) => studyPage(pageId));
  apiMocks.fetchDocumentProtectedImage.mockImplementation(async (_access, pageId: string) => `blob:${pageId}`);
  apiMocks.fetchDocumentSnapshot.mockImplementation(async (access: DocumentUploadData) => access);
  apiMocks.reprocessDocumentStudyLanguage.mockResolvedValue(undefined);
  pollingMocks.waitForDocumentPageResult.mockImplementation(async (_access, pageId: string) => studyPage(pageId));
  vi.stubGlobal("URL", { ...URL, revokeObjectURL: vi.fn() });
});

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("DocumentReader mutation guards", () => {
  it("keeps historical dictionary metadata readable without automatic dictionary reprojection", async () => {
    apiMocks.fetchDocumentPage.mockResolvedValue(
      studyPage("page-a", { studyLanguage: "en", requestedDictionaryLanguage: "de" }),
    );
    renderReader(documentAccess(), { preferredStudyLanguage: "en" });

    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");
    expect(screen.getByTestId("dictionary-requested")).toHaveTextContent("de");
    expect(apiMocks.reprocessDocumentStudyLanguage).not.toHaveBeenCalled();
  });

  it("does not enqueue redundant study work when the persisted child already matches", async () => {
    const user = userEvent.setup();
    apiMocks.fetchDocumentPage.mockResolvedValue(studyPage("page-a", { studyLanguage: "en" }));
    renderReader(documentAccess(), { preferredStudyLanguage: "en" });
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "study-en" }));

    expect(apiMocks.reprocessDocumentStudyLanguage).not.toHaveBeenCalled();
  });

  it("serializes study mutations while a child reprocess is active", async () => {
    const user = userEvent.setup();
    let resolveStudy: (() => void) | undefined;
    apiMocks.reprocessDocumentStudyLanguage.mockImplementation(
      () => new Promise<void>((resolve) => {
        resolveStudy = resolve;
      }),
    );
    pollingMocks.waitForDocumentPageResult.mockResolvedValue(
      studyPage("page-a", { studyLanguage: "en" }),
    );
    renderReader(documentAccess());
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "study-en" }));
    expect(screen.getByTestId("mutation")).toHaveTextContent("study");
    await user.click(screen.getByRole("button", { name: "study-en" }));
    expect(apiMocks.reprocessDocumentStudyLanguage).toHaveBeenCalledTimes(1);

    await act(async () => resolveStudy?.());
    await waitFor(() => expect(screen.getByTestId("mutation")).toHaveTextContent("none"));
  });

  it("rolls the study preference back after a non-API mutation failure", async () => {
    const user = userEvent.setup();
    const onStudy = vi.fn();
    apiMocks.reprocessDocumentStudyLanguage.mockRejectedValue(new Error("study failed"));
    renderReader(documentAccess(), { onStudy });
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "study-en" }));

    expect(await screen.findByTestId("study-error")).toBeVisible();
    expect(screen.getByTestId("reader-page")).toHaveTextContent("page-a");
    expect(onStudy).toHaveBeenNthCalledWith(1, "en");
    expect(onStudy).toHaveBeenLastCalledWith("pt-BR");
  });

  it("preserves the readable child and exposes an API study-language failure", async () => {
    const user = userEvent.setup();
    const onStudy = vi.fn();
    apiMocks.reprocessDocumentStudyLanguage.mockRejectedValue(new ApiError("rate_limited"));
    renderReader(documentAccess(), { onStudy });
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "study-en" }));

    expect(await screen.findByTestId("study-error")).toBeVisible();
    expect(screen.getByTestId("reader-page")).toHaveTextContent("page-a");
    expect(onStudy).toHaveBeenNthCalledWith(1, "en");
    expect(onStudy).toHaveBeenLastCalledWith("pt-BR");
  });

  it("does not let a late study mutation result replace a newly selected sibling", async () => {
    const user = userEvent.setup();
    let resolveRefresh: ((page: StudyPage) => void) | undefined;
    pollingMocks.waitForDocumentPageResult.mockImplementation(
      () => new Promise<StudyPage>((resolve) => {
        resolveRefresh = resolve;
      }),
    );
    const access = documentAccess(["page-a", "page-b"]);
    renderReader(access);
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");

    await user.click(screen.getByRole("button", { name: "study-en" }));
    await waitFor(() => expect(pollingMocks.waitForDocumentPageResult).toHaveBeenCalledTimes(1));
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-b");

    await act(async () => resolveRefresh?.(studyPage("page-a", { studyLanguage: "en" })));
    expect(screen.getByTestId("reader-page")).toHaveTextContent("page-b");
  });

  it("does not refetch when direct selection chooses the current page again", async () => {
    const user = userEvent.setup();
    renderReader(documentAccess());
    await screen.findByTestId("reader-page");
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /^Page 1:/ }));
    expect(apiMocks.fetchDocumentPage).toHaveBeenCalledTimes(1);
  });
});
