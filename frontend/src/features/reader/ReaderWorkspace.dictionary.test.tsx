import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { StudyPage } from "../../lib/api";
import { ReaderWorkspace } from "./ReaderWorkspace";

function page(requested: "de" | "pt-BR"): StudyPage {
  return {
    pageId: `page-${requested}`,
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage: "en",
    dictionaryLanguage: "en",
    requestedDictionaryLanguage: requested,
    fallbackDictionaryLanguage: "en",
    dictionarySources: [
      { ref: "de-source", dataset: "JMdict", productLanguage: "de", sourceVersion: "fixture", normalizedDigestSha256: "a".repeat(64) },
      { ref: "en-source", dataset: "JMdict", productLanguage: "en", sourceVersion: "fixture", normalizedDigestSha256: "b".repeat(64) },
    ],
    expiresAt: "2026-08-11T00:00:00Z",
    imageUrl: "/image",
    dimensions: { width: 100, height: 100 },
    error: null,
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "fixture" },
    regions: [{
      id: "region-1",
      text: "猫犬",
      rawText: "猫犬",
      correctedText: null,
      bbox: { x: 0, y: 0, width: 100, height: 100 },
      normalizedBbox: { x: 0, y: 0, width: 1, height: 1 },
      polygon: null,
      angle: 0,
      confidence: 1,
      readingOrder: 0,
      tokens: [],
      translation: null,
      explanation: null,
      grammar: [],
      vocabulary: requested === "de" ? [
        { id: "cat", surface: "猫", lemma: "猫", reading: "ねこ", meanings: ["Katze"], source: "JMdict", effectiveLanguage: "de", fallbackUsed: false, fallbackReason: null, sourceRef: "de-source", jlpt: null },
        { id: "dog", surface: "犬", lemma: "犬", reading: "いぬ", meanings: ["dog"], source: "JMdict", effectiveLanguage: "en", fallbackUsed: true, fallbackReason: "requested_entry_not_found", sourceRef: "en-source", jlpt: null },
      ] : [
        { id: "cat", surface: "猫", lemma: "猫", reading: "ねこ", meanings: ["cat"], source: "JMdict", effectiveLanguage: "en", fallbackUsed: true, fallbackReason: "unsupported_requested_language", sourceRef: "en-source", jlpt: null },
      ],
    }],
  };
}

function renderPage(studyPage: StudyPage) {
  render(
    <ReaderWorkspace
      page={studyPage}
      imageUrl="fixture-image"
      uiLocale="en"
      preferredStudyLanguage="en"
      preferredDictionaryLanguage={studyPage.requestedDictionaryLanguage ?? "en"}
      languageMutation={null}
      studyLanguageError={null}
      dictionaryLanguageError={null}
      onStudyLanguageChange={vi.fn()}
      onDictionaryLanguageChange={vi.fn()}
      onReset={vi.fn()}
    />,
  );
}

describe("ReaderWorkspace dictionary projection", () => {
  it("renders mixed German direct meanings and explicit English fallback", () => {
    renderPage(page("de"));
    expect(screen.getByText("Requested dictionary: German")).toBeVisible();
    expect(screen.getByText("Katze")).toHaveAttribute("lang", "de");
    expect(screen.getByText("dog")).toHaveAttribute("lang", "en");
    expect(screen.getByText("English fallback")).toBeVisible();
    expect(screen.getByText("JMdict · German")).toBeVisible();
    expect(screen.getByText("JMdict · English")).toBeVisible();
  });

  it("keeps pt-BR requested while labeling deterministic meanings as English", () => {
    renderPage(page("pt-BR"));
    expect(screen.getByText("Requested dictionary: Portuguese (Brazil)")).toBeVisible();
    expect(screen.getByText(/Deterministic Portuguese JMdict glosses are not available/)).toBeVisible();
    expect(screen.getByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByText("English fallback")).toBeVisible();
  });
});
