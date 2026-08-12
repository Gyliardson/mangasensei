import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_DICTIONARY_LANGUAGE,
  DICTIONARY_LANGUAGE_PREFERENCE_KEY,
  loadDictionaryLanguagePreference,
  saveDictionaryLanguagePreference,
} from "./dictionaryLanguage";

const originalDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");

function restoreLocalStorage() {
  if (originalDescriptor) Object.defineProperty(window, "localStorage", originalDescriptor);
}

beforeEach(() => {
  restoreLocalStorage();
  window.localStorage.clear();
});

afterEach(() => {
  restoreLocalStorage();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("dictionary language preference", () => {
  it.each(["de", "pt-BR", "es"])("normalizes legacy or malformed %s state to English", (value) => {
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, value);
    expect(loadDictionaryLanguagePreference()).toBe(DEFAULT_DICTIONARY_LANGUAGE);
  });

  it("persists only English", () => {
    saveDictionaryLanguagePreference("en");
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBe("en");
    expect(loadDictionaryLanguagePreference()).toBe("en");
  });

  it.each(["de", "pt-BR"] as const)("clears retired %s preference", (language) => {
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, "en");
    saveDictionaryLanguagePreference(language);
    expect(window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBeNull();
    expect(loadDictionaryLanguagePreference()).toBe("en");
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
      value: {
        setItem: () => { throw new Error("blocked"); },
        removeItem: () => { throw new Error("blocked"); },
      },
    });
    expect(() => saveDictionaryLanguagePreference("en")).not.toThrow();
    expect(() => saveDictionaryLanguagePreference("de")).not.toThrow();
  });
});
