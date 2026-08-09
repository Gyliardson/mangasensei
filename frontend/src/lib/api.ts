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
  | "expired";

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

export interface ReprocessData {
  readonly jobId: string;
  readonly status: JobStatus;
  readonly studyLanguage: StudyLanguage;
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
  readonly dictionaryLanguage: "en";
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
  constructor(readonly code: string, message: string) {
    super(message);
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

export async function reprocessStudyLanguage(
  upload: UploadData,
  studyLanguage: StudyLanguage,
  signal: AbortSignal,
): Promise<ReprocessData> {
  const response = await fetch(`/api/v1/pages/${encodeURIComponent(upload.pageId)}/reprocess`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": createIdempotencyKey("reprocess"),
      "X-Page-Token": upload.capabilities.reprocessPage,
    },
    body: JSON.stringify({ studyLanguage }),
    signal,
  });
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
    throw new ApiError("image_unavailable", "A imagem protegida não pôde ser carregada.");
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
    if (status.status === "failed" || status.status === "expired") {
      throw new ApiError(status.error?.code ?? status.status, status.error?.message ?? "Processamento encerrado.");
    }
    await abortableDelay(delay, signal);
    delay = Math.min(Math.round(delay * 1.6), 5_000);
  }
  throw new DOMException("Operação cancelada", "AbortError");
}

async function requestJson<T>(url: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {
    headers: { "X-Page-Token": token },
    signal,
  });
  return parseEnvelope<T>(response);
}

async function parseEnvelope<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok || !payload.success || payload.data === null) {
    throw new ApiError(
      payload.error?.code ?? "request_failed",
      payload.error?.message ?? "A requisição não pôde ser concluída.",
    );
  }
  return payload.data;
}

function validateClientFile(file: File): void {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new ApiError("invalid_image", "Use uma imagem JPEG, PNG ou WebP.");
  }
  if (file.size > 12 * 1024 * 1024) {
    throw new ApiError("image_too_large", "A imagem deve ter no máximo 12 MiB.");
  }
}

function createIdempotencyKey(namespace: "upload" | "reprocess"): string {
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
        reject(new DOMException("Operação cancelada", "AbortError"));
      },
      { once: true },
    );
  });
}
