import {
  ApiError,
  type DocumentSnapshot,
  type DocumentUploadData,
  type StudyPage,
  fetchDocumentPage,
  fetchDocumentSnapshot,
} from "./api";

const baseDelayMs = 600;
const maxDelayMs = 5_000;

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    }, { once: true });
  });
}

export function documentNeedsPolling(snapshot: DocumentSnapshot): boolean {
  return snapshot.status === "processing";
}

export async function waitForDocumentPageResult(
  access: DocumentUploadData,
  pageId: string,
  signal: AbortSignal,
  onSnapshot: (snapshot: DocumentSnapshot) => void,
  predicate: (page: StudyPage) => boolean,
): Promise<StudyPage> {
  let attempt = 0;
  while (!signal.aborted) {
    const snapshot = await fetchDocumentSnapshot(access, signal);
    onSnapshot(snapshot);
    const summary = snapshot.pages.find((candidate) => candidate.pageId === pageId);
    if (!summary) throw new ApiError("not_found");

    if (summary.status === "completed") {
      const page = await fetchDocumentPage(access, pageId, signal);
      if (predicate(page)) return page;
      throw new ApiError("request_failed");
    }

    if (
      summary.status === "failed" ||
      summary.status === "cancelled" ||
      summary.status === "expired"
    ) {
      if (summary.resultAvailable) {
        const page = await fetchDocumentPage(access, pageId, signal);
        throw new ApiError(page.error?.code ?? summary.status);
      }
      throw new ApiError(summary.status);
    }

    const backoff = Math.min(baseDelayMs * 1.6 ** attempt, maxDelayMs);
    attempt += 1;
    await delay(backoff, signal);
  }
  throw new DOMException("Aborted", "AbortError");
}
