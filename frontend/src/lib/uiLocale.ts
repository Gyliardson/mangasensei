export const UI_LOCALE_PREFERENCE_KEY = "mangasensei.ui.locale";

export const UI_LOCALES = ["en", "pt-BR"] as const;
export type UiLocale = (typeof UI_LOCALES)[number];

export const DEFAULT_UI_LOCALE: UiLocale = "en";

export function isUiLocale(value: unknown): value is UiLocale {
  return value === "en" || value === "pt-BR";
}

export function loadUiLocalePreference(): UiLocale {
  try {
    const stored = window.localStorage.getItem(UI_LOCALE_PREFERENCE_KEY);
    return isUiLocale(stored) ? stored : DEFAULT_UI_LOCALE;
  } catch {
    return DEFAULT_UI_LOCALE;
  }
}

export function saveUiLocalePreference(locale: UiLocale): void {
  try {
    window.localStorage.setItem(UI_LOCALE_PREFERENCE_KEY, locale);
  } catch {
    // Browser storage is a convenience only; English remains the deterministic fallback.
  }
}
