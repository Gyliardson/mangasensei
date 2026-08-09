import { describe, expect, it } from "vitest";

import { furiganaReading, isFuriganaMode, toHiragana } from "./furigana";

describe("furigana presentation", () => {
  it("converts ordinary katakana, small kana and long-vowel marks to learner-facing hiragana", () => {
    expect(toHiragana("キョウ")).toBe("きょう");
    expect(toHiragana("コーヒー")).toBe("こーひー");
  });

  it("suppresses kana-only script-equivalent readings in every visible mode", () => {
    expect(furiganaReading("です", "デス", "hiragana")).toBeNull();
    expect(furiganaReading("です", "デス", "katakana")).toBeNull();
    expect(furiganaReading("カタカナ", "カタカナ", "hiragana")).toBeNull();
  });

  it("presents useful kanji and mixed-token readings in the selected script", () => {
    expect(furiganaReading("猫", "ネコ", "hiragana")).toBe("ねこ");
    expect(furiganaReading("猫", "ネコ", "katakana")).toBe("ネコ");
    expect(furiganaReading("食べる", "タベル", "hiragana")).toBe("たべる");
    expect(furiganaReading("食べる", "タベル", "katakana")).toBe("タベル");
  });

  it("hides useful ruby without changing the surface contract", () => {
    expect(furiganaReading("猫", "ネコ", "hidden")).toBeNull();
  });

  it("accepts only the supported stored mode values", () => {
    expect(isFuriganaMode("hiragana")).toBe(true);
    expect(isFuriganaMode("katakana")).toBe(true);
    expect(isFuriganaMode("hidden")).toBe(true);
    expect(isFuriganaMode("natural")).toBe(false);
    expect(isFuriganaMode(null)).toBe(false);
  });
});
