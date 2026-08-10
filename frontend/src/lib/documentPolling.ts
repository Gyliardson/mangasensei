import {
  ApiError,
  type DocumentSnapshot,
  type DocumentUploadData,
  type JobStatus,
  type StudyPage,
  fetchDocumentPage,
  fetchDocumentSnapshot,
} from "./api";

const terminalStatuses = new Set<JobStatus>(["completed", "failed", "expired"]);

export function documentNeedsPolling(snapshot: DocumentSnapshot): boolean {
  return snapshot.pages.some((page) => !terminalStatuses.has(page.status));
}

export async function waitForDocumentPageResult(
  access: DocumentUploadData,
  pageId: string,
  signal: AbortSignal,
  onSnapshot: (snapshot: DocumentSnapshot) => void,
  isSatisfied: (page: StudyPage) => boolean,
): Promise<StudyPage> {
  let delay = 600;
  while (!signal.aborted) {
    const snapshot = await fetchDocumentSnapshot(access, signal);
    onSnapshot(snapshot);
    const summary = snapshot.pages.find((candidate) => candidate.pageId === pageId);
    if (!summary) throw new ApiError("not_found");

    if (summary.status === "completed") {
      const page = await fetchDocumentPage(access, pageId, signal);
      if (isSatisfied(page)) return page;
      throw new ApiError("request_failed");
    }
    if (summary.status === "failed" || summary.status === "expired") {
      if (summary.resultAvailable) {
        const page = await fetchDocumentPage(access, pageId, signal);
        throw new ApiError(page.error?.code ?? summary.status);
      }
      throw new ApiError(summary.status);
    }

    await abortableDelay(delay, signal);
    delay = Math.min(Math.round(delay * 1.6), 5_000);
  }
  throw new DOMException("Operation aborted", "AbortError");
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(resolve, milliseconds);
    signal.addEventListener(
      "abort",
      () => {
        window.clearTimeout(timeout);
        reject(new DOMException("Operation aborted", "AbortError"));
      },
      { once: true },
    );
  });
}
