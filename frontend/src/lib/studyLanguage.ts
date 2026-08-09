export const STUDY_LANGUAGE_PREFERENCE_KEY = "mangasensei.study.language";

export type StudyLanguage = "pt-BR" | "en";

export const DEFAULT_STUDY_LANGUAGE: StudyLanguage = "pt-BR";

export function isStudyLanguage(value: unknown): value is StudyLanguage {
  return value === "pt-BR" || value === "en";
}

export function studyLanguageLabel(language: StudyLanguage): string {
  return language === "en" ? "Inglês" : "Português (Brasil)";
}

export function loadStudyLanguagePreference(): StudyLanguage {
  try {
    const stored = window.localStorage.getItem(STUDY_LANGUAGE_PREFERENCE_KEY);
    return isStudyLanguage(stored) ? stored : DEFAULT_STUDY_LANGUAGE;
  } catch {
    return DEFAULT_STUDY_LANGUAGE;
  }
}

export function saveStudyLanguagePreference(language: StudyLanguage): void {
  try {
    window.localStorage.setItem(STUDY_LANGUAGE_PREFERENCE_KEY, language);
  } catch {
    // Browser storage is a convenience only; the persisted page result remains authoritative.
  }
}
