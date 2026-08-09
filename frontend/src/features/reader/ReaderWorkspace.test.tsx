import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { StudyPage, StudyRegion } from "../../lib/api";
import { ReaderWorkspace } from "./ReaderWorkspace";

function region(id: string, text: string, order: number): StudyRegion {
  return {
    id,
    text,
    rawText: text,
    correctedText: null,
    bbox: { x: 10, y: 20, width: 30, height: 40 },
    normalizedBbox: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
    polygon: null,
    angle: 0,
    confidence: 0.8,
    readingOrder: order,
    tokens: [
      {
        surface: text,
        lemma: text,
        reading: order === 0 ? text : "ヨミ",
        partOfSpeech: "名詞",
        dictionaryId: null,
      },
    ],
    translation: null,
    explanation: null,
    grammar: [],
    vocabulary: [],
  };
}

function page(regions: readonly StudyRegion[]): StudyPage {
  return {
    pageId: "page-001",
    status: "completed",
    resultAvailable: true,
    expiresAt: "2026-08-09T00:00:00Z",
    imageUrl: "/image",
    dimensions: { width: 100, height: 100 },
    regions,
    error: null,
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "commit" },
  };
}

describe("ReaderWorkspace", () => {
  it("shows an explicit empty state and resets to a new page", async () => {
    const onReset = vi.fn();
    const user = userEvent.setup();
    render(<ReaderWorkspace page={page([])} imageUrl="blob:image" onReset={onReset} />);

    expect(screen.getByText("Nenhuma região de texto foi reconhecida nesta página.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Nova página" }));
    expect(onReset).toHaveBeenCalledOnce();
  });

  it("changes regions by keyboard and renders fallback study content", async () => {
    const user = userEvent.setup();
    render(
      <ReaderWorkspace
        page={page([region("first", "猫", 0), region("second", "犬", 1)])}
        imageUrl="blob:image"
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "猫" })).toBeVisible();
    const second = screen.getByRole("button", { name: "Região 2: 犬" });
    second.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("heading", { name: /犬/ })).toBeVisible();
    expect(screen.getByText("ヨミ", { selector: "rt" })).toBeVisible();
    expect(screen.getByText("Análise contextual indisponível.")).toBeVisible();
    expect(screen.getByText("Nenhuma associação confiável ao dicionário.")).toBeVisible();
    expect(screen.getByText("Nenhum ponto gramatical adicional.")).toBeVisible();

    second.focus();
    await user.keyboard(" ");
    expect(second).toHaveAttribute("aria-pressed", "true");
  });

  it("uses API reading order for overlay numbers, initial selection and keyboard sequence", async () => {
    const user = userEvent.setup();
    render(
      <ReaderWorkspace
        page={page([
          region("top-right", "上右", 0),
          region("top-left", "上左", 1),
          region("lower-right", "下右", 2),
        ])}
        imageUrl="blob:image"
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByRole("heading", { name: "上右" })).toBeVisible();
    const controls = screen.getAllByRole("button", { name: /^Região \d:/ });
    expect(controls.map((control) => control.getAttribute("aria-label"))).toEqual([
      "Região 1: 上右",
      "Região 2: 上左",
      "Região 3: 下右",
    ]);

    controls[0].focus();
    await user.tab();
    expect(controls[1]).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("heading", { name: /上左/ })).toBeVisible();
  });

  it("shows local dictionary vocabulary when contextual AI is unavailable", () => {
    const localOnlyRegion: StudyRegion = {
      ...region("local", "猫", 0),
      tokens: [
        {
          surface: "猫",
          lemma: "猫",
          reading: "ネコ",
          partOfSpeech: "名詞",
          dictionaryId: "jmdict-1467640",
        },
      ],
      vocabulary: [
        {
          id: "jmdict-1467640",
          surface: "猫",
          lemma: "猫",
          reading: "ネコ",
          meanings: ["cat"],
          source: "JMdict",
          jlpt: { level: "N5", official: false },
        },
      ],
    };

    render(
      <ReaderWorkspace
        page={page([localOnlyRegion])}
        imageUrl="blob:image"
        onReset={vi.fn()}
      />,
    );

    expect(screen.getByText("Análise contextual indisponível.")).toBeVisible();
    expect(screen.getByText("cat")).toBeVisible();
    expect(screen.getByText("JMdict · JLPT N5 não oficial")).toBeVisible();
    expect(screen.queryByText("Nenhuma associação confiável ao dicionário.")).not.toBeInTheDocument();
  });
});
