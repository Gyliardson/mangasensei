import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_DICTIONARY_LANGUAGE,
  DICTIONARY_LANGUAGE_PREFERENCE_KEY,
  loadDictionaryLanguagePreference,
  saveDictionaryLanguagePreference,
} from "./dictionaryLanguage";

const originalDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");

afterEach(() => {
  window.localStorage.clear();
  vi.restoreAllMocks();
  if (originalDescriptor) Object.defineProperty(window, "localStorage", originalDescriptor);
});

describe("dictionary language preference", () => {
  it("defaults fresh and malformed state to English", () => {
    expect(loadDictionaryLanguagePreference()).toBe(DEFAULT_DICTIONARY_LANGUAGE);
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, "es");
    expect(loadDictionaryLanguagePreference()).toBe("en");
  });

  it.each(["en", "de", "pt-BR"] as const)("persists %s", (language) => {
    saveDictionaryLanguagePreference(language);
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe(language);
    expect(loadDictionaryLanguagePreference()).toBe(language);
  });

  it("survives storage read failure", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: { getItem: () => { throw new Error("blocked"); } },
    });
    expect(loadDictionaryLanguagePreference()).toBe("en");
  });

  it("survives storage write failure", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: { setItem: () => { throw new Error("blocked"); } },
    });
    expect(() => saveDictionaryLanguagePreference("de")).not.toThrow();
  });

  it("uses a storage key independent from study and UI preferences", () => {
    saveDictionaryLanguagePreference("de");
    window.localStorage.setItem("mangasensei.study.language", "pt-BR");
    window.localStorage.setItem("mangasensei.ui.locale", "pt-BR");
    expect(loadDictionaryLanguagePreference()).toBe("de");
  });
});
