export const DICTIONARY_LANGUAGE_PREFERENCE_KEY = "mangasensei.dictionary.language";

// Historical page payloads may still contain the old requested values. The active
// product preference is English-only; keeping the wider wire type avoids
// mis-parsing an unexpired historical result during an upgrade.
export type DictionaryLanguage = "en" | "de" | "pt-BR";

export const DEFAULT_DICTIONARY_LANGUAGE: DictionaryLanguage = "en";

export function isDictionaryLanguage(value: unknown): value is DictionaryLanguage {
  return value === "en";
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
    if (language === "en") {
      window.localStorage.setItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY, language);
      return;
    }
    window.localStorage.removeItem(DICTIONARY_LANGUAGE_PREFERENCE_KEY);
  } catch {
    // Browser storage is a convenience only; the persisted page result remains authoritative.
  }
}
