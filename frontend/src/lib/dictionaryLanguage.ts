export const LEGACY_DICTIONARY_LANGUAGE_PREFERENCE_KEY = "mangasensei.dictionary.language";

// Historical page payloads may still contain requested values emitted by older
// releases. This type is for wire/persisted metadata only, not new commands.
export type HistoricalDictionaryLanguage = "en" | "de" | "pt-BR";

// The deterministic local dictionary product surface is English-only.
export type ActiveDictionaryLanguage = "en";
export const ACTIVE_DICTIONARY_LANGUAGE: ActiveDictionaryLanguage = "en";

export function migrateLegacyDictionaryLanguagePreference(): ActiveDictionaryLanguage {
  try {
    window.localStorage.removeItem(LEGACY_DICTIONARY_LANGUAGE_PREFERENCE_KEY);
  } catch {
    // Browser storage is optional. Failure to remove a legacy key must not make
    // the application unusable or change the English-only active behavior.
  }
  return ACTIVE_DICTIONARY_LANGUAGE;
}
