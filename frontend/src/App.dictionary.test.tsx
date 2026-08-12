import { render, screen } from "@testing-library/react";
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

function page() {
  return {
    pageId: "page-001",
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage: "pt-BR",
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: "en",
    fallbackDictionaryLanguage: "en",
    dictionarySources: [{
      ref: "jmdict-en",
      dataset: "JMdict",
      productLanguage: "en",
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
        meanings: ["cat"],
        source: "JMdict",
        effectiveLanguage: "en",
        fallbackUsed: false,
        fallbackReason: null,
        sourceRef: "jmdict-en",
        jlpt: null,
      }],
    }],
  };
}

function successfulBaseFetch() {
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
      return Response.json({ success: true, data: page(), error: null });
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
}

describe("App English-only dictionary compatibility", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("normalizes a stored German preference to English without issuing dictionary reprojection", async () => {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, "pt-BR");
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, "de");
    const fetchMock = successfulBaseFetch();
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:image"), revokeObjectURL: vi.fn() });
    render(<App />);

    await uploadAndOpen();

    expect(screen.queryByRole("combobox", { name: "Idioma do dicionário" })).not.toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
    expect(screen.getByText("Dicionário solicitado: Inglês")).toBeVisible();
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("en");
    expect(
      fetchMock.mock.calls.some(([input, init]) =>
        String(input).endsWith("/reprocess")
        && (init as RequestInit | undefined)?.method === "POST"),
    ).toBe(false);
  });
});
