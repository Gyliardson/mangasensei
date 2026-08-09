import {
  DEFAULT_FURIGANA_MODE,
  type FuriganaMode,
  isFuriganaMode,
} from "./furigana";

export const FURIGANA_PREFERENCE_KEY = "mangasensei.reader.furigana";

interface PreferenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function browserStorage(): PreferenceStorage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadFuriganaPreference(
  storage: PreferenceStorage | null = browserStorage(),
): FuriganaMode {
  if (!storage) return DEFAULT_FURIGANA_MODE;
  try {
    const stored = storage.getItem(FURIGANA_PREFERENCE_KEY);
    return isFuriganaMode(stored) ? stored : DEFAULT_FURIGANA_MODE;
  } catch {
    return DEFAULT_FURIGANA_MODE;
  }
}

export function saveFuriganaPreference(
  mode: FuriganaMode,
  storage: PreferenceStorage | null = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(FURIGANA_PREFERENCE_KEY, mode);
  } catch {
    // Presentation preferences are best-effort and must never break the reader.
  }
}
