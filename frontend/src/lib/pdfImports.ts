import { ApiError, type DocumentSnapshot, type DocumentUploadData } from "./api";
import type { StudyLanguage } from "./studyLanguage";

export type PdfImportStatus = "queued" | "rendering" | "completed" | "failed";

interface Envelope<T> {
  readonly success: boolean;
  readonly data: T | null;
  readonly error: { readonly code: string; readonly message: string } | null;
}

interface PdfImportCreateData {
  readonly importId: string;
  readonly sourceKind: "pdf";
  readonly status: PdfImportStatus;
  readonly rasterContract: "pdfium-raster-v1";
  readonly expiresAt: string;
  readonly capabilities: { readonly readDocumentImport: string };
}

interface PdfImportViewData {
  readonly importId: string;
  readonly sourceKind: "pdf";
  readonly status: PdfImportStatus;
  readonly rasterContract: "pdfium-raster-v1";
  readonly pageCount: number | null;
  readonly errorCode: string | null;
  readonly createdAt: string;
  readonly expiresAt: string;
  readonly document: {
    readonly documentId: string;
    readonly capabilities: DocumentUploadData["capabilities"];
  } | null;
}

export async function uploadPdfImport(
  file: File,
  studyLanguage: StudyLanguage,
  signal: AbortSignal,
): Promise<PdfImportCreateData> {
  if (file.type !== "application/pdf") throw new ApiError("pdf_invalid");
  if (file.size > 256 * 1024 * 1024) throw new ApiError("pdf_invalid");
  const form = new FormData();
  form.set("pdf", file);
  form.set("studyLanguage", studyLanguage);
  const response = await fetch("/api/v1/document-imports", {
    method: "POST",
    headers: { "Idempotency-Key": createIdempotencyKey() },
    body: form,
    signal,
  });
  return parseEnvelope<PdfImportCreateData>(response);
}

export async function waitForPdfImport(
  created: PdfImportCreateData,
  signal: AbortSignal,
  onStatus: (status: PdfImportStatus) => void,
): Promise<DocumentUploadData> {
  let delay = 500;
  while (!signal.aborted) {
    const response = await fetch(
      `/api/v1/document-imports/${encodeURIComponent(created.importId)}`,
      {
        headers: { "X-Document-Import-Token": created.capabilities.readDocumentImport },
        signal,
      },
    );
    const view = await parseEnvelope<PdfImportViewData>(response);
    onStatus(view.status);
    if (view.status === "failed") throw new ApiError(view.errorCode ?? "pdf_render_failed");
    if (view.status === "completed") {
      if (view.document === null) throw new ApiError("pdf_manifest_invalid");
      const snapshotResponse = await fetch(
        `/api/v1/documents/${encodeURIComponent(view.document.documentId)}`,
        {
          headers: { "X-Document-Token": view.document.capabilities.readDocument },
          signal,
        },
      );
      const snapshot = await parseEnvelope<DocumentSnapshot>(snapshotResponse);
      return { ...snapshot, capabilities: view.document.capabilities };
    }
    await abortableDelay(delay, signal);
    delay = Math.min(Math.round(delay * 1.5), 2_000);
  }
  throw new DOMException("Operation aborted", "AbortError");
}

async function parseEnvelope<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as Envelope<T>;
  if (!response.ok || !payload.success || payload.data === null) {
    throw new ApiError(payload.error?.code ?? "request_failed");
  }
  return payload.data;
}

function createIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return `pdf-import-${crypto.randomUUID()}`;
  const random = crypto.getRandomValues(new Uint8Array(16));
  return `pdf-import-${Array.from(random, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
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
