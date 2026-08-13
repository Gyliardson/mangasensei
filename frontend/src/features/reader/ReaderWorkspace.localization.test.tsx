import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { StudyPage } from "../../lib/api";
import { ReaderWorkspace } from "./ReaderWorkspace";

const PAGE: StudyPage = {
  pageId: "page-localization",
  status: "completed",
  resultAvailable: true,
  contentLanguage: "ja",
  studyLanguage: "pt-BR",
  dictionaryLanguage: "en",
  expiresAt: "2026-08-10T00:00:00Z",
  imageUrl: "/image",
  dimensions: { width: 100, height: 100 },
  error: null,
  ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "commit" },
  regions: [
    {
      id: "region-localization",
      text: "猫です",
      rawText: "猫です",
      correctedText: null,
      bbox: { x: 0, y: 0, width: 100, height: 100 },
      normalizedBbox: { x: 0, y: 0, width: 1, height: 1 },
      polygon: null,
      angle: 0,
      confidence: 0.97,
      readingOrder: 0,
      tokens: [
        {
          surface: "猫",
          lemma: "猫",
          reading: "ネコ",
          partOfSpeech: "名詞",
          dictionaryId: "jmdict-1467640",
        },
      ],
      translation: "É um gato.",
      explanation: "Frase nominal polida.",
      grammar: [],
      vocabulary: [
        {
          id: "jmdict-1467640",
          surface: "猫",
          lemma: "猫",
          reading: "ネコ",
          meanings: ["cat"],
          source: "JMdict",
          jlpt: null,
        },
      ],
    },
  ],
};

function workspace(uiLocale: "en" | "pt-BR") {
  return (
    <ReaderWorkspace
      page={PAGE}
      imageUrl="fixture-image"
      uiLocale={uiLocale}
      preferredStudyLanguage="pt-BR"
      languageMutation={null}
      studyLanguageError={null}
      onStudyLanguageChange={vi.fn()}
      onReset={vi.fn()}
    />
  );
}

describe("ReaderWorkspace UI localization", () => {
  it("renders English reader chrome while preserving study, dictionary, and Japanese semantics", () => {
    render(workspace("en"));

    expect(screen.getByRole("heading", { name: "Select a region" })).toBeVisible();
    expect(screen.getByRole("group", { name: "Study preferences" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Study language" })).toHaveValue("pt-BR");
    expect(screen.queryByRole("combobox", { name: "Dictionary language" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Region 1: 猫です" })).toBeVisible();
    expect(screen.getByRole("button", { name: "New page" })).toBeVisible();
    expect(screen.getByRole("heading", { name: /猫/ })).toHaveAttribute("lang", "ja");
    expect(screen.getByText("ネコ")).toHaveAttribute("lang", "ja");
    expect(screen.getByText("É um gato.")).toHaveAttribute("lang", "pt-BR");
    expect(screen.getByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByText("Requested dictionary: English")).toBeVisible();
  });

  it("renders Brazilian Portuguese reader chrome without changing semantic language annotations", () => {
    render(workspace("pt-BR"));

    expect(screen.getByRole("heading", { name: "Selecione uma região" })).toBeVisible();
    expect(screen.getByRole("group", { name: "Preferências de estudo" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
    expect(screen.queryByRole("combobox", { name: "Idioma do dicionário" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Região 1: 猫です" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Nova página" })).toBeVisible();
    expect(screen.getByRole("heading", { name: /猫/ })).toHaveAttribute("lang", "ja");
    expect(screen.getByText("É um gato.")).toHaveAttribute("lang", "pt-BR");
    expect(screen.getByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByText("Dicionário solicitado: Inglês")).toBeVisible();
  });
});
