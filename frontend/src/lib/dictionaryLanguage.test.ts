import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ACTIVE_DICTIONARY_LANGUAGE,
  LEGACY_DICTIONARY_LANGUAGE_PREFERENCE_KEY,
  migrateLegacyDictionaryLanguagePreference,
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

describe("legacy dictionary language preference migration", () => {
  it.each([undefined, "en", "de", "pt-BR", "malformed"])(
    "retires legacy browser state %s without persisting a replacement preference",
    (value) => {
      if (value !== undefined) {
        window.localStorage.setItem(LEGACY_DICTIONARY_LANGUAGE_PREFERENCE_KEY, value);
      }

      expect(migrateLegacyDictionaryLanguagePreference()).toBe(ACTIVE_DICTIONARY_LANGUAGE);
      expect(window.localStorage.getItem(LEGACY_DICTIONARY_LANGUAGE_PREFERENCE_KEY)).toBeNull();
    },
  );

  it("keeps English-only behavior when storage removal fails", () => {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: {
        removeItem: () => {
          throw new Error("blocked");
        },
      },
    });

    expect(migrateLegacyDictionaryLanguagePreference()).toBe("en");
  });
});
