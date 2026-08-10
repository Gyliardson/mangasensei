import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type DocumentUploadData,
  type StudyPage,
} from "../../lib/api";
import type { DictionaryLanguage } from "../../lib/dictionaryLanguage";
import type { StudyLanguage } from "../../lib/studyLanguage";
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
  ReaderWorkspace: ({
    page,
    languageMutation,
    studyLanguageError,
    dictionaryLanguageError,
    onStudyLanguageChange,
    onDictionaryLanguageChange,
  }: {
    page: StudyPage;
    languageMutation: "study" | "dictionary" | null;
    studyLanguageError: string | null;
    dictionaryLanguageError: string | null;
    onStudyLanguageChange: (language: StudyLanguage) => void;
    onDictionaryLanguageChange: (language: DictionaryLanguage) => void;
  }) => (
    <div>
      <div data-testid="reader-page">{page.pageId}</div>
      <div data-testid="mutation">{languageMutation ?? "none"}</div>
      {studyLanguageError ? <div data-testid="study-error">{studyLanguageError}</div> : null}
      {dictionaryLanguageError ? <div data-testid="dictionary-error">{dictionaryLanguageError}</div> : null}
      <button type="button" onClick={() => onStudyLanguageChange("en")}>study-en</button>
      <button type="button" onClick={() => onDictionaryLanguageChange("de")}>dictionary-de</button>
    </div>
  ),
}));

const capabilities = {
  readDocument: "read-document",
  readDocumentImage: "read-image",
  reprocessDocument: "reprocess-document",
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
    },
    capabilities,
  };
}

function renderReader(
  access: DocumentUploadData,
  options: {
    preferredStudyLanguage?: StudyLanguage;
    preferredDictionaryLanguage?: DictionaryLanguage;
    onStudy?: (language: StudyLanguage) => void;
    onDictionary?: (language: DictionaryLanguage) => void;
  } = {},
) {
  return render(
    <DocumentReader
      access={access}
      uiLocale="en"
      preferredStudyLanguage={options.preferredStudyLanguage ?? "pt-BR"}
      preferredDictionaryLanguage={options.preferredDictionaryLanguage ?? "en"}
      onPreferredStudyLanguageChange={options.onStudy ?? vi.fn()}
      onPreferredDictionaryLanguageChange={options.onDictionary ?? vi.fn()}
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
  apiMocks.reprocessDocumentDictionaryLanguage.mockResolvedValue(undefined);
  pollingMocks.waitForDocumentPageResult.mockImplementation(async (_access, pageId: string) => studyPage(pageId));
  vi.stubGlobal("URL", { ...URL, revokeObjectURL: vi.fn() });
});

afterEach(() => {
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("DocumentReader mutation guards", () => {
  it("does not enqueue redundant language mutations when the persisted child already matches", async () => {
    const user = userEvent.setup();
    apiMocks.fetchDocumentPage.mockResolvedValue(
      studyPage("page-a", { studyLanguage: "en", requestedDictionaryLanguage: "de" }),
    );
    const access = documentAccess();
    renderReader(access, {
      preferredStudyLanguage: "en",
      preferredDictionaryLanguage: "de",
    });
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "study-en" }));
    await user.click(screen.getByRole("button", { name: "dictionary-de" }));

    expect(apiMocks.reprocessDocumentStudyLanguage).not.toHaveBeenCalled();
    expect(apiMocks.reprocessDocumentDictionaryLanguage).not.toHaveBeenCalled();
  });

  it("serializes child mutations instead of starting dictionary work during study reprocessing", async () => {
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
    const access = documentAccess();
    renderReader(access);
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "study-en" }));
    expect(screen.getByTestId("mutation")).toHaveTextContent("study");
    await user.click(screen.getByRole("button", { name: "dictionary-de" }));
    expect(apiMocks.reprocessDocumentDictionaryLanguage).not.toHaveBeenCalled();

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
    expect(onStudy).toHaveBeenNthCalledWith(1, "en");
    expect(onStudy).toHaveBeenLastCalledWith("pt-BR");
  });

  it("preserves the readable child and exposes an API dictionary failure", async () => {
    const user = userEvent.setup();
    const onDictionary = vi.fn();
    apiMocks.reprocessDocumentDictionaryLanguage.mockRejectedValue(new ApiError("rate_limited"));
    renderReader(documentAccess(), { onDictionary });
    await screen.findByTestId("reader-page");

    await user.click(screen.getByRole("button", { name: "dictionary-de" }));

    expect(await screen.findByTestId("dictionary-error")).toBeVisible();
    expect(screen.getByTestId("reader-page")).toHaveTextContent("page-a");
    expect(onDictionary).toHaveBeenNthCalledWith(1, "de");
    expect(onDictionary).toHaveBeenLastCalledWith("en");
  });

  it("does not let a late mutation result replace a newly selected sibling", async () => {
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

  it("reports automatic dictionary reprojection failure without hiding the readable child", async () => {
    apiMocks.reprocessDocumentDictionaryLanguage.mockRejectedValue(new Error("projection failed"));
    renderReader(documentAccess(), { preferredDictionaryLanguage: "de" });

    expect(await screen.findByTestId("reader-page")).toHaveTextContent("page-a");
    expect(await screen.findByTestId("dictionary-error")).toBeVisible();
    expect(apiMocks.reprocessDocumentDictionaryLanguage).toHaveBeenCalledTimes(1);
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
