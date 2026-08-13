import type { HistoricalDictionaryLanguage } from "./dictionaryLanguage";
import type { StudyLanguage } from "./studyLanguage";

export type JobStatus =
  | "pending"
  | "claimed"
  | "processing_ocr"
  | "processing_linguistics"
  | "processing_gemini"
  | "completed"
  | "retryable_failure"
  | "failed"
  | "cancelled"
  | "expired";

export type DocumentAggregateStatus = "processing" | "completed" | "completed_with_errors" | "cancelled";
export type EffectiveDictionaryLanguage = "en" | "de";
export type DictionaryFallbackReason =
  | "unsupported_requested_language"
  | "requested_entry_not_found"
  | "requested_form_not_found"
  | "requested_glosses_not_found";

export interface DictionarySourceReference {
  readonly ref: string;
  readonly dataset: "JMdict";
  readonly productLanguage: EffectiveDictionaryLanguage;
  readonly sourceVersion: string;
  readonly normalizedDigestSha256: string;
}

export interface CapabilityTokens {
  readonly readPage: string;
  readonly readImage: string;
  readonly reprocessPage: string;
}

export interface UploadData {
  readonly pageId: string;
  readonly jobId: string;
  readonly contentSha256: string;
  readonly width: number;
  readonly height: number;
  readonly mediaType: string;
  readonly expiresAt: string;
  readonly studyLanguage: StudyLanguage;
  readonly capabilities: CapabilityTokens;
}

export interface DocumentCapabilityTokens {
  readonly readDocument: string;
  readonly readDocumentImage: string;
  readonly reprocessDocument: string;
  readonly manageDocument: string;
}

export interface DocumentPageSummary {
  readonly pageId: string;
  readonly ordinal: number;
  readonly status: JobStatus;
  readonly resultAvailable: boolean;
}

export interface DocumentProgress {
  readonly totalPages: number;
  readonly completedPages: number;
  readonly processingPages: number;
  readonly failedPages: number;
  readonly cancelledPages: number;
}

export interface DocumentSnapshot {
  readonly documentId: string;
  readonly sourceKind: "images";
  readonly expiresAt: string;
  readonly orderRevision: number;
  readonly status: DocumentAggregateStatus;
  readonly pages: readonly DocumentPageSummary[];
  readonly progress: DocumentProgress;
}

export interface DocumentUploadData extends DocumentSnapshot {
  readonly capabilities: DocumentCapabilityTokens;
}

export interface RetryFailedDocumentData {
  readonly created: boolean;
  readonly retriedPageIds: readonly string[];
  readonly jobIds: readonly string[];
  readonly status: DocumentAggregateStatus;
  readonly progress: DocumentProgress;
}

export interface CancelDocumentData {
  readonly cancelledPages: number;
  readonly cancelRequestedPages: number;
  readonly status: DocumentAggregateStatus;
  readonly progress: DocumentProgress;
}

export interface ReprocessData {
  readonly jobId: string;
  readonly status: JobStatus;
  readonly studyLanguage: StudyLanguage;
  readonly requestedDictionaryLanguage?: HistoricalDictionaryLanguage;
  readonly created: boolean;
}

export interface StudyToken {
  readonly surface: string;
  readonly lemma: string;
  readonly reading: string;
  readonly partOfSpeech: string;
  readonly dictionaryId: string | null;
}

export interface VocabularyItem {
  readonly id: string;
  readonly surface: string;
  readonly lemma: string;
  readonly reading: string;
  readonly meanings: readonly string[];
  readonly source: string;
  readonly effectiveLanguage?: EffectiveDictionaryLanguage;
  readonly fallbackUsed?: boolean;
  readonly fallbackReason?: DictionaryFallbackReason | null;
  readonly sourceRef?: string | null;
  readonly jlpt: { readonly level: string; readonly official: false } | null;
}

export interface StudyRegion {
  readonly id: string;
  readonly text: string;
  readonly rawText: string;
  readonly correctedText: string | null;
  readonly bbox: { readonly x: number; readonly y: number; readonly width: number; readonly height: number };
  readonly normalizedBbox: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly polygon: readonly (readonly [number, number])[] | null;
  readonly angle: number;
  readonly confidence: number;
  readonly readingOrder: number;
  readonly tokens: readonly StudyToken[];
  readonly translation: string | null;
  readonly explanation: string | null;
  readonly grammar: readonly string[];
  readonly vocabulary: readonly VocabularyItem[];
}

export interface StudyPage {
  readonly pageId: string;
  readonly status: JobStatus;
  readonly resultAvailable: boolean;
  readonly contentLanguage: "ja";
  readonly studyLanguage: StudyLanguage;
  /** Legacy English-only StudyResult field. */
  readonly dictionaryLanguage: "en";
  readonly requestedDictionaryLanguage?: HistoricalDictionaryLanguage;
  readonly fallbackDictionaryLanguage?: "en";
  readonly dictionarySources?: readonly DictionarySourceReference[];
  readonly expiresAt: string;
  readonly imageUrl: string;
  readonly dimensions: { readonly width: number; readonly height: number };
  readonly regions: readonly StudyRegion[];
  readonly error: { readonly code: string; readonly message: string } | null;
  readonly ocr: {
    readonly detector: string;
    readonly recognizer: string;
    readonly upstreamCommit: string;
  };
}

interface PageStatus {
  readonly status: JobStatus;
  readonly resultAvailable: boolean;
  readonly error: StudyPage["error"];
}

interface Envelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: { readonly code: string; readonly message: string } | null;
}

export class ApiError extends Error {
  constructor(readonly code: string) {
    super(code);
    this.name = "ApiError";
  }
}

export async function uploadPage(
  file: File,
  studyLanguage: StudyLanguage,
  signal: AbortSignal,
): Promise<UploadData> {
  validateClientFile(file);
  const form = new FormData();
  form.set("image", file);
  form.set("studyLanguage", studyLanguage);
  const response = await fetch("/api/v1/pages", {
    method: "POST",
    headers: { "Idempotency-Key": createIdempotencyKey("upload") },
    body: form,
    signal,
  });
  return parseEnvelope<UploadData>(response);
}

export async function uploadDocument(
  files: readonly File[],
  studyLanguage: StudyLanguage,
  signal: AbortSignal,
): Promise<DocumentUploadData> {
  if (files.length < 2) throw new ApiError("document_requires_multiple_images");
  const form = new FormData();
  for (const file of files) {
    validateClientFile(file);
    form.append("images[]", file);
  }
  form.set("studyLanguage", studyLanguage);
  const response = await fetch("/api/v1/documents", {
    method: "POST",
    headers: { "Idempotency-Key": createIdempotencyKey("document-upload") },
    body: form,
    signal,
  });
  return parseEnvelope<DocumentUploadData>(response);
}

export async function fetchDocumentSnapshot(
  access: DocumentUploadData,
  signal: AbortSignal,
): Promise<DocumentSnapshot> {
  return requestDocumentJson<DocumentSnapshot>(
    `/api/v1/documents/${encodeURIComponent(access.documentId)}`,
    access.capabilities.readDocument,
    signal,
  );
}

export async function fetchDocumentPage(
  access: DocumentUploadData,
  pageId: string,
  signal: AbortSignal,
): Promise<StudyPage> {
  return requestDocumentJson<StudyPage>(
    `/api/v1/documents/${encodeURIComponent(access.documentId)}/pages/${encodeURIComponent(pageId)}`,
    access.capabilities.readDocument,
    signal,
  );
}

export async function fetchDocumentProtectedImage(
  access: DocumentUploadData,
  pageId: string,
  signal: AbortSignal,
): Promise<string> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(access.documentId)}/pages/${encodeURIComponent(pageId)}/image`,
    {
      headers: { "X-Document-Token": access.capabilities.readDocumentImage },
      signal,
    },
  );
  if (!response.ok) throw new ApiError("image_unavailable");
  return URL.createObjectURL(await response.blob());
}

export async function retryFailedDocumentPages(
  access: DocumentUploadData,
  signal: AbortSignal,
): Promise<RetryFailedDocumentData> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(access.documentId)}/retry-failed`,
    {
      method: "POST",
      headers: {
        "Idempotency-Key": createIdempotencyKey("document-retry-failed"),
        "X-Document-Token": access.capabilities.manageDocument,
      },
      signal,
    },
  );
  return parseEnvelope<RetryFailedDocumentData>(response);
}

export async function cancelDocumentProcessing(
  access: DocumentUploadData,
  signal: AbortSignal,
): Promise<CancelDocumentData> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(access.documentId)}/cancel`,
    {
      method: "POST",
      headers: { "X-Document-Token": access.capabilities.manageDocument },
      signal,
    },
  );
  return parseEnvelope<CancelDocumentData>(response);
}

export async function reorderDocument(
  access: DocumentUploadData,
  pageIds: readonly string[],
  expectedOrderRevision: number,
  signal: AbortSignal,
): Promise<DocumentSnapshot> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(access.documentId)}/order`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-Document-Token": access.capabilities.manageDocument,
      },
      body: JSON.stringify({ pageIds, expectedOrderRevision }),
      signal,
    },
  );
  return parseEnvelope<DocumentSnapshot>(response);
}

export async function reprocessStudyLanguage(
  upload: UploadData,
  studyLanguage: StudyLanguage,
  signal: AbortSignal,
): Promise<ReprocessData> {
  const response = await fetch(`/api/v1/pages/${encodeURIComponent(upload.pageId)}/reprocess`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": createIdempotencyKey("study-reprocess"),
      "X-Page-Token": upload.capabilities.reprocessPage,
    },
    body: JSON.stringify({ studyLanguage }),
    signal,
  });
  return parseEnvelope<ReprocessData>(response);
}

export async function reprocessDocumentStudyLanguage(
  access: DocumentUploadData,
  pageId: string,
  studyLanguage: StudyLanguage,
  signal: AbortSignal,
): Promise<ReprocessData> {
  const response = await fetch(
    `/api/v1/documents/${encodeURIComponent(access.documentId)}/pages/${encodeURIComponent(pageId)}/reprocess`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": createIdempotencyKey("document-study-reprocess"),
        "X-Document-Token": access.capabilities.reprocessDocument,
      },
      body: JSON.stringify({ studyLanguage }),
      signal,
    },
  );
  return parseEnvelope<ReprocessData>(response);
}

export async function fetchProtectedImage(
  pageId: string,
  token: string,
  signal: AbortSignal,
): Promise<string> {
  const response = await fetch(`/api/v1/pages/${encodeURIComponent(pageId)}/image`, {
    headers: { "X-Page-Token": token },
    signal,
  });
  if (!response.ok) {
    throw new ApiError("image_unavailable");
  }
  return URL.createObjectURL(await response.blob());
}

export async function waitForPage(
  upload: UploadData,
  signal: AbortSignal,
  onStatus: (status: JobStatus) => void,
): Promise<StudyPage> {
  let delay = 600;
  while (!signal.aborted) {
    const status = await requestJson<PageStatus>(
      `/api/v1/pages/${encodeURIComponent(upload.pageId)}/status`,
      upload.capabilities.readPage,
      signal,
    );
    onStatus(status.status);
    if (status.status === "completed") {
      return requestJson<StudyPage>(
        `/api/v1/pages/${encodeURIComponent(upload.pageId)}`,
        upload.capabilities.readPage,
        signal,
      );
    }
    if (status.status === "failed" || status.status === "cancelled" || status.status === "expired") {
      throw new ApiError(status.error?.code ?? status.status);
    }
    await abortableDelay(delay, signal);
    delay = Math.min(Math.round(delay * 1.6), 5_000);
  }
  throw new DOMException("Operation aborted", "AbortError");
}

export async function waitForDocumentPage(
  access: DocumentUploadData,
  pageId: string,
  signal: AbortSignal,
  onStatus: (status: JobStatus) => void,
  isSatisfied: (page: StudyPage) => boolean,
): Promise<StudyPage> {
  let delay = 600;
  while (!signal.aborted) {
    const page = await fetchDocumentPage(access, pageId, signal);
    onStatus(page.status);
    if (page.status === "completed" && isSatisfied(page)) return page;
    if (page.status === "failed" || page.status === "cancelled" || page.status === "expired") {
      throw new ApiError(page.error?.code ?? page.status);
    }
    await abortableDelay(delay, signal);
    delay = Math.min(Math.round(delay * 1.6), 5_000);
  }
  throw new DOMException("Operation aborted", "AbortError");
}

async function requestJson<T>(url: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    headers: { "X-Page-Token": token },
    signal,
  });
  return parseEnvelope<T>(response);
}

async function requestDocumentJson<T>(url: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    headers: { "X-Document-Token": token },
    signal,
  });
  return parseEnvelope<T>(response);
}

async function parseEnvelope<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok || !payload.success || payload.data === null) {
    throw new ApiError(payload.error?.code ?? "request_failed");
  }
  return payload.data;
}

function validateClientFile(file: File): void {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new ApiError("invalid_image");
  }
  if (file.size > 12 * 1024 * 1024) {
    throw new ApiError("image_too_large");
  }
}

type IdempotencyNamespace =
  | "upload"
  | "document-upload"
  | "document-retry-failed"
  | "study-reprocess"
  | "document-study-reprocess";

function createIdempotencyKey(namespace: IdempotencyNamespace): string {
  if (typeof crypto.randomUUID === "function") {
    return `${namespace}-${crypto.randomUUID()}`;
  }
  const random = crypto.getRandomValues(new Uint8Array(16));
  return `${namespace}-${Array.from(random, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
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
