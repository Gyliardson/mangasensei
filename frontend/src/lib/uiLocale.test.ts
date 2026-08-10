import { afterEach, describe, expect, it, vi } from "vitest";

import {
  DEFAULT_UI_LOCALE,
  UI_LOCALE_PREFERENCE_KEY,
  isUiLocale,
  loadUiLocalePreference,
  saveUiLocalePreference,
} from "./uiLocale";

describe("UI-locale preference", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("defaults to English and accepts only supported UI locales", () => {
    expect(DEFAULT_UI_LOCALE).toBe("en");
    expect(isUiLocale("en")).toBe(true);
    expect(isUiLocale("pt-BR")).toBe(true);
    expect(isUiLocale("es")).toBe(false);
    expect(isUiLocale(null)).toBe(false);
  });

  it("persists and restores Brazilian Portuguese", () => {
    saveUiLocalePreference("pt-BR");

    expect(window.localStorage.getItem(UI_LOCALE_PREFERENCE_KEY)).toBe("pt-BR");
    expect(loadUiLocalePreference()).toBe("pt-BR");
  });

  it("falls back to English for invalid or unavailable storage", () => {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, "es");
    expect(loadUiLocalePreference()).toBe("en");

    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    expect(loadUiLocalePreference()).toBe("en");

    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    expect(() => saveUiLocalePreference("pt-BR")).not.toThrow();
  });
});
