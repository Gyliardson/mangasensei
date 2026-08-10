import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StudyPage } from "../../lib/api";
import { ReaderWorkspace } from "./ReaderWorkspace";

const LEXICAL_PAGE: StudyPage = {
  pageId: "page-lexical",
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
      id: "region-lexical",
      text: "表記一表記二なんとか",
      rawText: "表記一表記二なんとか",
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
      vocabulary: [
        {
          id: "jmdict-shared",
          surface: "表記一",
          lemma: "表記一",
          reading: "よみ",
          meanings: ["first form"],
          source: "JMdict",
          jlpt: null,
        },
        {
          id: "jmdict-shared",
          surface: "表記二",
          lemma: "表記二",
          reading: "よみ",
          meanings: ["second form"],
          source: "JMdict",
          jlpt: null,
        },
        {
          id: "jmdict-1188420",
          surface: "なんとか",
          lemma: "なんとか",
          reading: "なんとか",
          meanings: ["somehow"],
          source: "JMdict",
          jlpt: null,
        },
      ],
    },
  ],
};

const noop = () => undefined;

describe("ReaderWorkspace lexical identity", () => {
  it("renders distinct canonical forms and a resolved multi-token vocabulary match", () => {
    render(
      <ReaderWorkspace
        page={LEXICAL_PAGE}
        imageUrl="fixture-image"
        uiLocale="pt-BR"
        preferredStudyLanguage="pt-BR"
        studyLanguageUpdating={false}
        studyLanguageError={null}
        onStudyLanguageChange={noop}
        onReset={noop}
      />,
    );

    expect(screen.getByText("表記一")).toBeVisible();
    expect(screen.getByText("表記二")).toBeVisible();
    expect(screen.getByText("first form")).toBeVisible();
    expect(screen.getByText("second form")).toBeVisible();
    for (const element of screen.getAllByText("なんとか")) {
      expect(element).toBeVisible();
    }
    expect(screen.getByText("somehow")).toBeVisible();
  });
});
