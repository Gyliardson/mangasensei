import { describe, expect, it, vi } from "vitest";

import { FURIGANA_PREFERENCE_KEY, loadFuriganaPreference, saveFuriganaPreference } from "./furiganaPreference";

describe("furigana preference storage", () => {
  it("defaults to hiragana for missing or malformed stored values", () => {
    expect(loadFuriganaPreference({ getItem: () => null, setItem: vi.fn() })).toBe("hiragana");
    expect(loadFuriganaPreference({ getItem: () => "natural", setItem: vi.fn() })).toBe("hiragana");
  });

  it("restores every supported presentation mode", () => {
    for (const mode of ["hiragana", "katakana", "hidden"] as const) {
      expect(loadFuriganaPreference({ getItem: () => mode, setItem: vi.fn() })).toBe(mode);
    }
  });

  it("falls back safely when browser storage cannot be read", () => {
    expect(
      loadFuriganaPreference({
        getItem: () => {
          throw new DOMException("blocked");
        },
        setItem: vi.fn(),
      }),
    ).toBe("hiragana");
  });

  it("persists supported modes and ignores storage write failures", () => {
    const setItem = vi.fn();
    saveFuriganaPreference("katakana", { getItem: vi.fn(), setItem });
    expect(setItem).toHaveBeenCalledWith(FURIGANA_PREFERENCE_KEY, "katakana");

    expect(() =>
      saveFuriganaPreference("hidden", {
        getItem: vi.fn(),
        setItem: () => {
          throw new DOMException("quota exceeded");
        },
      }),
    ).not.toThrow();
  });
});
