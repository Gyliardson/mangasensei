import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { DICTIONARY_LANGUAGE_PREFERENCE_KEY } from "./lib/dictionaryLanguage";
import { UI_LOCALE_PREFERENCE_KEY } from "./lib/uiLocale";

function uploadData() {
  return {
    pageId: "page-001",
    jobId: "job-001",
    contentSha256: "a".repeat(64),
    width: 80,
    height: 120,
    mediaType: "image/png",
    expiresAt: "2026-08-11T00:00:00Z",
    studyLanguage: "pt-BR",
    capabilities: {
      readPage: "read-page-token",
      readImage: "read-image-token",
      reprocessPage: "reprocess-token",
    },
  };
}

function page(requested: "en" | "de" | "pt-BR" = "en") {
  const german = requested === "de";
  return {
    pageId: "page-001",
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage: "pt-BR",
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: requested,
    fallbackDictionaryLanguage: "en",
    dictionarySources: [{
      ref: german ? "jmdict-de" : "jmdict-en",
      dataset: "JMdict",
      productLanguage: german ? "de" : "en",
      sourceVersion: "fixture",
      normalizedDigestSha256: "b".repeat(64),
    }],
    expiresAt: "2026-08-11T00:00:00Z",
    imageUrl: "/api/v1/pages/page-001/image",
    dimensions: { width: 80, height: 120 },
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "fixture" },
    error: null,
    regions: [{
      id: "region-001",
      text: "猫",
      rawText: "猫",
      correctedText: null,
      bbox: { x: 0, y: 0, width: 80, height: 120 },
      normalizedBbox: { x: 0, y: 0, width: 1, height: 1 },
      polygon: null,
      angle: 0,
      confidence: 1,
      readingOrder: 0,
      tokens: [{ surface: "猫", lemma: "猫", reading: "ネコ", partOfSpeech: "名詞", dictionaryId: "cat" }],
      translation: "É um gato.",
      explanation: null,
      grammar: [],
      vocabulary: [{
        id: "cat",
        surface: "猫",
        lemma: "猫",
        reading: "ネコ",
        meanings: [german ? "Katze" : "cat"],
        source: "JMdict",
        effectiveLanguage: german ? "de" : "en",
        fallbackUsed: false,
        fallbackReason: null,
        sourceRef: german ? "jmdict-de" : "jmdict-en",
        jlpt: null,
      }],
    }],
  };
}

function successfulBaseFetch(current: () => "en" | "de" | "pt-BR") {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/v1/pages" && init?.method === "POST") {
      return Response.json({ success: true, data: uploadData(), error: null }, { status: 202 });
    }
    if (url.endsWith("/image")) return new Response("image");
    if (url.endsWith("/status")) {
      return Response.json({ success: true, data: { status: "completed", resultAvailable: true, error: null }, error: null });
    }
    if (url === "/api/v1/pages/page-001") {
      return Response.json({ success: true, data: page(current()), error: null });
    }
    return new Response(null, { status: 404 });
  });
}

async function uploadAndOpen() {
  const user = userEvent.setup();
  await user.upload(
    screen.getByLabelText("Imagem da página"),
    new File(["image"], "page.png", { type: "image/png" }),
  );
  await user.click(screen.getByRole("button", { name: "Analisar página" }));
  expect(await screen.findByText("cat")).toHaveAttribute("lang", "en");
  return user;
}

describe("App dictionary language", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("switches English to German through dictionary-only reprojection", async () => {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, "pt-BR");
    let requested: "en" | "de" = "en";
    const base = successfulBaseFetch(() => requested);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/reprocess")) {
        expect(JSON.parse(String(init?.body))).toEqual({ dictionaryLanguage: "de" });
        expect(String(init?.body)).not.toContain("studyLanguage");
        requested = "de";
        return Response.json({ success: true, data: { jobId: "job-de", status: "pending", studyLanguage: "pt-BR", requestedDictionaryLanguage: "de", created: true }, error: null }, { status: 202 });
      }
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:image"), revokeObjectURL: vi.fn() });
    render(<App />);

    const user = await uploadAndOpen();
    const dictionary = screen.getByRole("combobox", { name: "Idioma do dicionário" });
    expect(dictionary).toHaveValue("en");
    await user.selectOptions(dictionary, "de");

    expect(await screen.findByText("Katze")).toHaveAttribute("lang", "de");
    expect(screen.getByText("Dicionário solicitado: Alemão")).toBeVisible();
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("de");
  });

  it("keeps the completed result visible and rolls back the preference when reprojection fails", async () => {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, "pt-BR");
    const base = successfulBaseFetch(() => "en");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/reprocess")) {
        return Response.json({ success: false, data: null, error: { code: "processing_failed", message: "failed" } }, { status: 409 });
      }
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:image"), revokeObjectURL: vi.fn() });
    render(<App />);

    const user = await uploadAndOpen();
    await user.selectOptions(screen.getByRole("combobox", { name: "Idioma do dicionário" }), "de");

    expect(await screen.findByRole("alert")).toHaveTextContent("O processamento da página falhou.");
    expect(screen.getByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByRole("combobox", { name: "Idioma do dicionário" })).toHaveValue("en");
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("en");
  });

  it("shows the initial completed English result while honoring a stored German preference", async () => {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, "pt-BR");
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, "de");
    let requested: "en" | "de" = "en";
    let resolveReprocess!: (response: Response) => void;
    const reprocessResponse = new Promise<Response>((resolve) => { resolveReprocess = resolve; });
    const base = successfulBaseFetch(() => requested);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/reprocess")) {
        expect(JSON.parse(String(init?.body))).toEqual({ dictionaryLanguage: "de" });
        return reprocessResponse;
      }
      return base(input, init);
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:image"), revokeObjectURL: vi.fn() });
    render(<App />);

    const user = userEvent.setup();
    await user.upload(screen.getByLabelText("Imagem da página"), new File(["image"], "page.png", { type: "image/png" }));
    await user.click(screen.getByRole("button", { name: "Analisar página" }));

    expect(await screen.findByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByText(/resultado concluído em Inglês continua visível/)).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Idioma do dicionário" })).toHaveValue("de");
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toBeDisabled();

    requested = "de";
    resolveReprocess(Response.json({ success: true, data: { jobId: "job-de", status: "pending", studyLanguage: "pt-BR", requestedDictionaryLanguage: "de", created: true }, error: null }, { status: 202 }));

    await waitFor(() => expect(screen.getByText("Katze")).toHaveAttribute("lang", "de"));
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("de");
  });
});
