import { describe, expect, it } from "vitest";

import { furiganaReading, toHiragana } from "./furigana";

describe("furigana presentation", () => {
  it("converts ordinary katakana, small kana and long-vowel marks to learner-facing hiragana", () => {
    expect(toHiragana("キョウ" )).toBe("きょう");
    expect(toHiragana("コーヒー")).toBe("こーひー");
  });

  it("suppresses kana-only script-equivalent readings", () => {
    expect(furiganaReading("です", "デス")).toBeNull();
    expect(furiganaReading("カタカナ", "カタカナ")).toBeNull();
  });

  it("keeps useful kanji and mixed-token readings", () => {
    expect(furiganaReading("猫", "ネコ")).toBe("ねこ");
    expect(furiganaReading("食べる", "タベル")).toBe("たべる");
  });
});
