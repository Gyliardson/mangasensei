import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StudyPage, StudyRegion } from "../../lib/api";
import { ReaderWorkspace } from "./ReaderWorkspace";
import { READER_VIEWPORT_PREFERENCE_KEY } from "./readerViewportPreference";

function fixtureRegion(): StudyRegion {
  return {
    id: "region-001",
    text: "猫",
    rawText: "猫",
    correctedText: null,
    bbox: { x: 10, y: 20, width: 30, height: 40 },
    normalizedBbox: { x: 0.1, y: 0.2, width: 0.3, height: 0.4 },
    polygon: null,
    angle: 0,
    confidence: 0.9,
    readingOrder: 0,
    tokens: [
      {
        surface: "猫",
        lemma: "猫",
        reading: "ネコ",
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

function fixturePage(): StudyPage {
  return {
    pageId: "page-viewport",
    status: "completed",
    resultAvailable: true,
    expiresAt: "2026-08-09T00:00:00Z",
    imageUrl: "/image",
    dimensions: { width: 1600, height: 2400 },
    regions: [fixtureRegion()],
    error: null,
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "commit" },
  };
}

describe("ReaderWorkspace viewport controls", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("switches fit mode, adjusts zoom and restores the local presentation preference", async () => {
    const user = userEvent.setup();
    const first = render(
      <ReaderWorkspace page={fixturePage()} imageUrl="fixture-image" onReset={vi.fn()} />,
    );

    const fitMode = screen.getByRole("combobox", { name: "Ajuste da página" });
    expect(fitMode).toHaveValue("comfortable");
    expect(screen.getByLabelText("Nível de zoom")).toHaveTextContent("100%");

    await user.selectOptions(fitMode, "width");
    await user.click(screen.getByRole("button", { name: "Aumentar zoom" }));

    expect(screen.getByLabelText("Nível de zoom")).toHaveTextContent("125%");
    expect(document.querySelector(".page-canvas")).toHaveAttribute("data-fit-mode", "width");
    expect(document.querySelector(".page-canvas")).toHaveAttribute("data-zoom", "125");
    expect(JSON.parse(window.localStorage.getItem(READER_VIEWPORT_PREFERENCE_KEY) ?? "{}")).toEqual({
      fitMode: "width",
      zoom: 125,
    });

    first.unmount();
    render(<ReaderWorkspace page={fixturePage()} imageUrl="fixture-image" onReset={vi.fn()} />);

    expect(screen.getByRole("combobox", { name: "Ajuste da página" })).toHaveValue("width");
    expect(screen.getByLabelText("Nível de zoom")).toHaveTextContent("125%");
  });

  it("exposes keyboard-focusable fit, zoom and scroll controls", async () => {
    const user = userEvent.setup();
    render(<ReaderWorkspace page={fixturePage()} imageUrl="fixture-image" onReset={vi.fn()} />);

    const fitMode = screen.getByRole("combobox", { name: "Ajuste da página" });
    fitMode.focus();
    expect(fitMode).toHaveFocus();

    await user.tab();
    expect(screen.getByRole("button", { name: "Diminuir zoom" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Aumentar zoom" })).toHaveFocus();

    screen.getByRole("region", { name: "Visualização da página" }).focus();
    expect(screen.getByRole("region", { name: "Visualização da página" })).toHaveFocus();
  });
});
