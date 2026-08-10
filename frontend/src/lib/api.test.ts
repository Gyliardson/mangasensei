import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  type UploadData,
  fetchProtectedImage,
  reprocessDictionaryLanguage,
  reprocessStudyLanguage,
  uploadPage,
  waitForPage,
} from "./api";

const upload: UploadData = {
  pageId: "page-001",
  jobId: "job-001",
  contentSha256: "a".repeat(64),
  width: 80,
  height: 120,
  mediaType: "image/png",
  expiresAt: "2026-08-09T00:00:00Z",
  studyLanguage: "pt-BR",
  capabilities: {
    readPage: "read-page-token",
    readImage: "read-image-token",
    reprocessPage: "reprocess-token",
  },
};

function envelope(data: unknown, status = 200): Response {
  return Response.json({ success: true, data, error: null }, { status });
}

describe("API client", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("uploads a validated image with explicit study language and an idempotency key", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => envelope(upload, 202),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000001"),
      getRandomValues: vi.fn(),
    });
    const file = new File(["image"], "page.png", { type: "image/png" });

    await expect(
      uploadPage(file, "pt-BR", new AbortController().signal),
    ).resolves.toEqual(upload);

    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(options.headers).toEqual({
      "Idempotency-Key": "upload-00000000-0000-4000-8000-000000000001",
    });
    expect(options.body).toBeInstanceOf(FormData);
    const form = options.body as FormData;
    expect(form.get("image")).toBe(file);
    expect(form.get("studyLanguage")).toBe("pt-BR");
  });

  it("reprocesses study language with the page capability and a distinct idempotency key", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      envelope(
        {
          jobId: "job-002",
          status: "pending",
          studyLanguage: "en",
          created: true,
        },
        202,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000002"),
      getRandomValues: vi.fn(),
    });

    await expect(
      reprocessStudyLanguage(upload, "en", new AbortController().signal),
    ).resolves.toMatchObject({ studyLanguage: "en", created: true });

    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/pages/page-001/reprocess");
    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(options.method).toBe("POST");
    expect(options.headers).toEqual({
      "Content-Type": "application/json",
      "Idempotency-Key": "study-reprocess-00000000-0000-4000-8000-000000000002",
      "X-Page-Token": "reprocess-token",
    });
    expect(options.body).toBe(JSON.stringify({ studyLanguage: "en" }));
  });

  it("reprojects dictionary language without sending a study-language axis", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
      envelope(
        {
          jobId: "job-003",
          status: "pending",
          studyLanguage: "pt-BR",
          requestedDictionaryLanguage: "de",
          created: true,
        },
        202,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000003"),
      getRandomValues: vi.fn(),
    });

    await expect(
      reprocessDictionaryLanguage(upload, "de", new AbortController().signal),
    ).resolves.toMatchObject({ requestedDictionaryLanguage: "de", created: true });

    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(options.headers).toEqual({
      "Content-Type": "application/json",
      "Idempotency-Key": "dictionary-reprocess-00000000-0000-4000-8000-000000000003",
      "X-Page-Token": "reprocess-token",
    });
    expect(options.body).toBe(JSON.stringify({ dictionaryLanguage: "de" }));
    expect(String(options.body)).not.toContain("studyLanguage");
  });

  it("propagates dictionary reprojection errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({
        success: false,
        data: null,
        error: { code: "analysis_in_progress", message: "busy" },
      }, { status: 409 })),
    );

    await expect(
      reprocessDictionaryLanguage(upload, "pt-BR", new AbortController().signal),
    ).rejects.toEqual(new ApiError("analysis_in_progress"));
  });

  it("uses cryptographic bytes when randomUUID is unavailable", async () => {
    const fetchMock = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => envelope(upload, 202),
    );
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("crypto", {
      getRandomValues: vi.fn((bytes: Uint8Array) => {
        bytes.fill(15);
        return bytes;
      }),
    });

    await uploadPage(
      new File(["image"], "page.webp", { type: "image/webp" }),
      "en",
      new AbortController().signal,
    );

    const options = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(options.headers).toEqual({ "Idempotency-Key": `upload-${"0f".repeat(16)}` });
    expect((options.body as FormData).get("studyLanguage")).toBe("en");
  });

  it("rejects unsupported and oversized files before fetching", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const signal = new AbortController().signal;

    await expect(
      uploadPage(new File(["text"], "page.txt", { type: "text/plain" }), "pt-BR", signal),
    ).rejects.toMatchObject({ code: "invalid_image" });
    await expect(
      uploadPage(
        new File([new Uint8Array(12 * 1024 * 1024 + 1)], "large.png", { type: "image/png" }),
        "pt-BR",
        signal,
      ),
    ).rejects.toMatchObject({ code: "image_too_large" });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("surfaces stable API error codes without coupling presentation copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            success: false,
            data: null,
            error: { code: "invalid_request", message: "Requisição inválida." },
          },
          { status: 422 },
        ),
      ),
    );

    await expect(
      uploadPage(
        new File(["image"], "page.png", { type: "image/png" }),
        "pt-BR",
        new AbortController().signal,
      ),
    ).rejects.toEqual(new ApiError("invalid_request"));
  });

  it("loads a protected image as an object URL and rejects failed downloads", async () => {
    const createObjectURL = vi.fn(() => "blob:protected");
    vi.stubGlobal("URL", { ...URL, createObjectURL });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("image")));

    await expect(
      fetchProtectedImage("page-001", "token", new AbortController().signal),
    ).resolves.toBe("blob:protected");
    expect(createObjectURL).toHaveBeenCalledOnce();

    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 404 })));
    await expect(
      fetchProtectedImage("page-001", "token", new AbortController().signal),
    ).rejects.toMatchObject({ code: "image_unavailable" });
  });

  it("polls until completion and publishes every status", async () => {
    vi.useFakeTimers();
    const statuses: string[] = [];
    const studyPage = {
      pageId: "page-001",
      status: "completed",
      resultAvailable: true,
      contentLanguage: "ja",
      studyLanguage: "pt-BR",
      dictionaryLanguage: "en",
      expiresAt: upload.expiresAt,
      imageUrl: "/image",
      dimensions: { width: 80, height: 120 },
      regions: [],
      error: null,
      ocr: { detector: "default", recognizer: "48px", upstreamCommit: "commit" },
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(envelope({ status: "processing_ocr", resultAvailable: false, error: null }))
        .mockResolvedValueOnce(envelope({ status: "completed", resultAvailable: true, error: null }))
        .mockResolvedValueOnce(envelope(studyPage)),
    );

    const result = waitForPage(upload, new AbortController().signal, (status) =>
      statuses.push(status),
    );
    await vi.advanceTimersByTimeAsync(600);

    await expect(result).resolves.toEqual(studyPage);
    expect(statuses).toEqual(["processing_ocr", "completed"]);
  });

  it("stops on terminal failure and supports cancellation during backoff", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        envelope({
          status: "failed",
          resultAvailable: false,
          error: { code: "ocr_failed", message: "OCR falhou." },
        }),
      ),
    );
    await expect(
      waitForPage(upload, new AbortController().signal, vi.fn()),
    ).rejects.toEqual(new ApiError("ocr_failed"));

    const controller = new AbortController();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => envelope({ status: "pending", resultAvailable: false, error: null })),
    );
    const pending = waitForPage(upload, controller.signal, vi.fn());
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("rejects immediately when polling starts with an aborted signal", async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(waitForPage(upload, controller.signal, vi.fn())).rejects.toMatchObject({
      name: "AbortError",
    });
  });
});
