export const DICTIONARY_LANGUAGE_PREFERENCE_KEY = "mangasensei.dictionary.language";

export type DictionaryLanguage = "en" | "de" | "pt-BR";

export const DEFAULT_DICTIONARY_LANGUAGE: DictionaryLanguage = "en";

export function isDictionaryLanguage(value: unknown): value is DictionaryLanguage {
  return value === "en" || value === "de" || value === "pt-BR";
}

export function loadDictionaryLanguagePreference(): DictionaryLanguage {
  try {
    const stored = window.localStorage.getItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY);
    return isDictionaryLanguage(stored) ? stored : DEFAULT_DICTIONARY_LANGUAGE;
  } catch {
    return DEFAULT_DICTIONARY_LANGUAGE;
  }
}

export function saveDictionaryLanguagePreference(language: DictionaryLanguage): void {
  try {
    window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, language);
  } catch {
    // Browser storage is a convenience only; the persisted page result remains authoritative.
  }
}
