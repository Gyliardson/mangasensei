import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { StudyPage, StudyRegion } from "../../lib/api";
import { ReaderWorkspace } from "./ReaderWorkspace";
import { FURIGANA_PREFERENCE_KEY } from "./furiganaPreference";

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

function page(regions: readonly StudyRegion[], studyLanguage: StudyPage["studyLanguage"] = "pt-BR"): StudyPage {
  return {
    pageId: "page-001",
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage,
    dictionaryLanguage: "en",
    expiresAt: "2026-08-09T00:00:00Z",
    imageUrl: "/image",
    dimensions: { width: 100, height: 100 },
    regions,
    error: null,
    ocr: { detector: "fixture", recognizer: "fixture", upstreamCommit: "commit" },
  };
}

function renderWorkspace(
  studyPage: StudyPage,
  options: {
    readonly preferredStudyLanguage?: StudyPage["studyLanguage"];
    readonly studyLanguageUpdating?: boolean;
    readonly studyLanguageError?: string | null;
    readonly onStudyLanguageChange?: (language: StudyPage["studyLanguage"]) => void;
    readonly onReset?: () => void;
  } = {},
) {
  return render(
    <ReaderWorkspace
      page={studyPage}
      imageUrl="fixture-image"
      preferredStudyLanguage={options.preferredStudyLanguage ?? studyPage.studyLanguage}
      studyLanguageUpdating={options.studyLanguageUpdating ?? false}
      studyLanguageError={options.studyLanguageError ?? null}
      onStudyLanguageChange={options.onStudyLanguageChange ?? vi.fn()}
      onReset={options.onReset ?? vi.fn()}
    />,
  );
}

describe("ReaderWorkspace", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("shows an explicit empty state and resets to a new page", async () => {
    const onReset = vi.fn();
    const user = userEvent.setup();
    renderWorkspace(page([]), { onReset });

    expect(screen.getByText("Nenhuma região de texto foi reconhecida nesta página.")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Nova página" }));
    expect(onReset).toHaveBeenCalledOnce();
  });

  it("changes regions by keyboard and renders fallback study content", async () => {
    const user = userEvent.setup();
    renderWorkspace(page([region("first", "猫", 0), region("second", "犬", 1)]));

    expect(screen.getByRole("heading", { name: "猫" })).toBeVisible();
    const second = screen.getByRole("button", { name: "Região 2: 犬" });
    second.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByRole("heading", { name: /犬/ })).toBeVisible();
    expect(screen.getByText("よみ", { selector: "rt" })).toBeVisible();
    expect(screen.getByText("Análise contextual indisponível.")).toBeVisible();
    expect(screen.getByText("Nenhuma associação confiável ao dicionário.")).toBeVisible();
    expect(screen.getByText("Nenhum ponto gramatical adicional.")).toBeVisible();

    second.focus();
    await user.keyboard(" ");
    expect(second).toHaveAttribute("aria-pressed", "true");
  });

  it("uses API reading order for overlay numbers, initial selection and keyboard sequence", async () => {
    const user = userEvent.setup();
    renderWorkspace(
      page([
        region("top-right", "上右", 0),
        region("top-left", "上左", 1),
        region("lower-right", "下右", 2),
      ]),
    );

    expect(screen.getByRole("heading", { name: "上右" })).toBeVisible();
    const controls = screen.getAllByRole("button", { name: /^Região \d:/ });
    expect(controls.map((control) => control.getAttribute("aria-label"))).toEqual([
      "Região 1: 上右",
      "Região 2: 上左",
      "Região 3: 下右",
    ]);

    const firstControl = screen.getByRole("button", { name: "Região 1: 上右" });
    const secondControl = screen.getByRole("button", { name: "Região 2: 上左" });
    firstControl.focus();
    await user.tab();
    expect(secondControl).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("heading", { name: /上左/ })).toBeVisible();
  });

  it("suppresses ruby when a kana-only surface differs only by script", () => {
    const kanaRegion: StudyRegion = {
      ...region("kana", "です", 0),
      tokens: [
        {
          surface: "です",
          lemma: "です",
          reading: "デス",
          partOfSpeech: "助動詞",
          dictionaryId: null,
        },
      ],
    };

    renderWorkspace(page([kanaRegion]));

    expect(screen.getByRole("heading", { name: "です" })).toBeVisible();
    expect(document.querySelector("rt")).not.toBeInTheDocument();
  });

  it("switches furigana script, hides ruby and persists the presentation preference", async () => {
    const user = userEvent.setup();
    const preferenceRegion: StudyRegion = {
      ...region("preference", "猫です食べる", 0),
      tokens: [
        {
          surface: "猫",
          lemma: "猫",
          reading: "ネコ",
          partOfSpeech: "名詞",
          dictionaryId: null,
        },
        {
          surface: "です",
          lemma: "です",
          reading: "デス",
          partOfSpeech: "助動詞",
          dictionaryId: null,
        },
        {
          surface: "食べる",
          lemma: "食べる",
          reading: "タベル",
          partOfSpeech: "動詞",
          dictionaryId: null,
        },
      ],
    };

    const first = renderWorkspace(page([preferenceRegion]));
    const select = screen.getByRole("combobox", { name: "Exibição de furigana" });

    expect(select).toHaveValue("hiragana");
    expect(screen.getByText("ねこ", { selector: "rt" })).toBeVisible();
    expect(screen.getByText("たべる", { selector: "rt" })).toBeVisible();
    expect(screen.queryByText("デス", { selector: "rt" })).not.toBeInTheDocument();

    await user.selectOptions(select, "katakana");
    expect(screen.getByText("ネコ", { selector: "rt" })).toBeVisible();
    expect(screen.getByText("タベル", { selector: "rt" })).toBeVisible();
    expect(screen.queryByText("デス", { selector: "rt" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(FURIGANA_PREFERENCE_KEY)).toBe("katakana");

    first.unmount();
    renderWorkspace(page([preferenceRegion]));
    const restored = screen.getByRole("combobox", { name: "Exibição de furigana" });
    expect(restored).toHaveValue("katakana");
    expect(screen.getByText("ネコ", { selector: "rt" })).toBeVisible();

    await user.selectOptions(restored, "hidden");
    expect(document.querySelector("rt")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "猫です食べる" })).toBeVisible();
    expect(window.localStorage.getItem(FURIGANA_PREFERENCE_KEY)).toBe("hidden");
  });

  it("falls back to hiragana when the stored preference is stale", () => {
    window.localStorage.setItem(FURIGANA_PREFERENCE_KEY, "natural");
    const localOnlyRegion: StudyRegion = {
      ...region("stale", "猫", 0),
      tokens: [
        {
          surface: "猫",
          lemma: "猫",
          reading: "ネコ",
          partOfSpeech: "名詞",
          dictionaryId: null,
        },
      ],
    };

    renderWorkspace(page([localOnlyRegion]));

    expect(screen.getByRole("combobox", { name: "Exibição de furigana" })).toHaveValue("hiragana");
    expect(screen.getByText("ねこ", { selector: "rt" })).toBeVisible();
  });

  it("shows local English dictionary meanings independently from contextual study language", () => {
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
      translation: "É um gato.",
      explanation: "Frase nominal polida.",
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

    renderWorkspace(page([localOnlyRegion]));

    expect(screen.getByText("É um gato.")).toHaveAttribute("lang", "pt-BR");
    expect(screen.getByText("Frase nominal polida.")).toHaveAttribute("lang", "pt-BR");
    expect(screen.getByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByText("significados locais em inglês")).toBeVisible();
    expect(screen.getByText("JMdict · JLPT N5 não oficial")).toBeVisible();
  });

  it("requests a new study language without reinterpreting the current result", async () => {
    const user = userEvent.setup();
    const onStudyLanguageChange = vi.fn();
    const studyPage = page([region("language", "猫", 0)]);
    const rendered = renderWorkspace(studyPage, { onStudyLanguageChange });

    const select = screen.getByRole("combobox", { name: "Idioma de estudo" });
    expect(select).toHaveValue("pt-BR");
    await user.selectOptions(select, "en");
    expect(onStudyLanguageChange).toHaveBeenCalledWith("en");

    rendered.rerender(
      <ReaderWorkspace
        page={studyPage}
        imageUrl="fixture-image"
        preferredStudyLanguage="en"
        studyLanguageUpdating
        studyLanguageError={null}
        onStudyLanguageChange={onStudyLanguageChange}
        onReset={vi.fn()}
      />,
    );

    expect(select).toHaveValue("en");
    expect(select).toBeDisabled();
    expect(
      screen.getByText(/O resultado exibido continua em Português \(Brasil\)/),
    ).toBeVisible();
    expect(screen.getByText(/estudo Português \(Brasil\)/)).toBeVisible();
  });
});
