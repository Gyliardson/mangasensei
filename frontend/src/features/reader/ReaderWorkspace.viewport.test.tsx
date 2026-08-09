import { render, screen, waitFor, within } from "@testing-library/react";
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
    contentLanguage: "ja",
    studyLanguage: "pt-BR",
    dictionaryLanguage: "en",
    expiresAt: "2026-08-09T00:00:00Z",
    imageUrl: "/image",
    dimensions: { width: 1600, height: 2400 },
    regions: [fixtureRegion()],
    error: null,
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "commit" },
  };
}

function renderReader() {
  const studyPage = fixturePage();
  return render(
    <ReaderWorkspace
      page={studyPage}
      imageUrl="fixture-image"
      preferredStudyLanguage={studyPage.studyLanguage}
      studyLanguageUpdating={false}
      studyLanguageError={null}
      onStudyLanguageChange={vi.fn()}
      onReset={vi.fn()}
    />,
  );
}

describe("ReaderWorkspace viewport controls", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 1024 });
    Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: 768 });
  });

  it("separates study, navigation and page-presentation responsibilities", () => {
    renderReader();

    const studyControls = screen.getByRole("group", { name: "Preferências de estudo" });
    expect(within(studyControls).getByRole("combobox", { name: "Idioma de estudo" })).toBeVisible();
    expect(within(studyControls).getByRole("combobox", { name: "Exibição de furigana" })).toBeVisible();

    const navigation = screen.getByRole("group", { name: "Navegação" });
    expect(within(navigation).getByRole("button", { name: "Nova página" })).toBeVisible();
    expect(within(navigation).queryByRole("combobox")).not.toBeInTheDocument();

    const presentation = screen.getByRole("group", { name: "Apresentação da página" });
    expect(within(presentation).getByRole("combobox", { name: "Ajuste da página" })).toBeVisible();
    expect(within(presentation).getByRole("group", { name: "Zoom da página" })).toBeVisible();
    expect(within(presentation).queryByRole("combobox", { name: "Idioma de estudo" })).not.toBeInTheDocument();
  });

  it("switches fit mode, adjusts zoom and restores the local presentation preference", async () => {
    const user = userEvent.setup();
    const first = renderReader();

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
    renderReader();

    expect(screen.getByRole("combobox", { name: "Ajuste da página" })).toHaveValue("width");
    expect(screen.getByLabelText("Nível de zoom")).toHaveTextContent("125%");
  });

  it("only exposes the manga viewport as a focusable scroll region when horizontal pan is needed", async () => {
    const user = userEvent.setup();
    renderReader();

    const viewport = document.querySelector<HTMLElement>(".page-viewport");
    expect(viewport).not.toBeNull();
    await waitFor(() => expect(viewport).toHaveAttribute("data-horizontal-pan", "false"));
    expect(viewport).not.toHaveAttribute("tabindex");
    expect(screen.queryByRole("region", { name: /Visualização da página/ })).not.toBeInTheDocument();

    const fitMode = screen.getByRole("combobox", { name: "Ajuste da página" });
    await user.selectOptions(fitMode, "width");
    await user.click(screen.getByRole("button", { name: "Aumentar zoom" }));

    await waitFor(() => expect(viewport).toHaveAttribute("data-horizontal-pan", "true"));
    const scrollRegion = screen.getByRole("region", { name: "Visualização da página com rolagem horizontal" });
    expect(scrollRegion).toHaveAttribute("tabindex", "0");
    scrollRegion.focus();
    expect(scrollRegion).toHaveFocus();
  });

  it("presents a stored comfortable preference as width on mobile without rewriting storage", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: 390 });
    Object.defineProperty(window, "innerHeight", { configurable: true, writable: true, value: 844 });
    window.localStorage.setItem(
      READER_VIEWPORT_PREFERENCE_KEY,
      JSON.stringify({ fitMode: "comfortable", zoom: 100 }),
    );

    renderReader();

    const fitMode = screen.getByRole("combobox", { name: "Ajuste da página" });
    await waitFor(() => expect(fitMode).toHaveValue("width"));
    expect(within(fitMode).queryByRole("option", { name: "Confortável" })).not.toBeInTheDocument();
    expect(within(fitMode).getByRole("option", { name: "Largura" })).toBeVisible();
    expect(within(fitMode).getByRole("option", { name: "Página inteira" })).toBeVisible();
    expect(window.localStorage.getItem(READER_VIEWPORT_PREFERENCE_KEY)).toBe(
      JSON.stringify({ fitMode: "comfortable", zoom: 100 }),
    );
  });
});
