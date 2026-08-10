import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_STUDY_LANGUAGE,
  STUDY_LANGUAGE_PREFERENCE_KEY,
  isStudyLanguage,
  loadStudyLanguagePreference,
  saveStudyLanguagePreference,
} from "./studyLanguage";

describe("study-language preference", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("defaults to pt-BR and accepts only the supported study languages", () => {
    expect(DEFAULT_STUDY_LANGUAGE).toBe("pt-BR");
    expect(isStudyLanguage("pt-BR")).toBe(true);
    expect(isStudyLanguage("en")).toBe(true);
    expect(isStudyLanguage("es")).toBe(false);
    expect(isStudyLanguage(null)).toBe(false);
  });

  it("persists and restores English", () => {
    saveStudyLanguagePreference("en");

    expect(window.localStorage.getItem(STUDY_LANGUAGE_PREFERENCE_KEY)).toBe("en");
    expect(loadStudyLanguagePreference()).toBe("en");
  });

  it("falls back deterministically for malformed or unavailable storage", () => {
    window.localStorage.setItem(STUDY_LANGUAGE_PREFERENCE_KEY, "es");
    expect(loadStudyLanguagePreference()).toBe("pt-BR");

    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    expect(loadStudyLanguagePreference()).toBe("pt-BR");

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    expect(() => saveStudyLanguagePreference("en")).not.toThrow();
  });
});
