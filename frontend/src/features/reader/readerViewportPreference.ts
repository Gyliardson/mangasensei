import {
  DEFAULT_READER_FIT_MODE,
  type ReaderFitMode,
  clampReaderZoom,
  isReaderFitMode,
} from "./readerViewport";

export const READER_VIEWPORT_PREFERENCE_KEY = "mangasensei.reader.viewport";

export interface ReaderViewportPreference {
  readonly fitMode: ReaderFitMode;
  readonly zoom: number;
}

interface PreferenceStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const DEFAULT_PREFERENCE: ReaderViewportPreference = {
  fitMode: DEFAULT_READER_FIT_MODE,
  zoom: 100,
};

function browserStorage(): PreferenceStorage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadReaderViewportPreference(
  storage: PreferenceStorage | null = browserStorage(),
): ReaderViewportPreference {
  if (!storage) return { ...DEFAULT_PREFERENCE };

  try {
    const stored = storage.getItem(READER_VIEWPORT_PREFERENCE_KEY);
    if (!stored) return { ...DEFAULT_PREFERENCE };

    const parsed: unknown = JSON.parse(stored);
    if (!isPreferenceRecord(parsed)) return { ...DEFAULT_PREFERENCE };
    if (!isReaderFitMode(parsed.fitMode)) return { ...DEFAULT_PREFERENCE };
    if (typeof parsed.zoom !== "number" || !Number.isFinite(parsed.zoom)) {
      return { ...DEFAULT_PREFERENCE };
    }

    return {
      fitMode: parsed.fitMode,
      zoom: clampReaderZoom(parsed.zoom),
    };
  } catch {
    return { ...DEFAULT_PREFERENCE };
  }
}

export function saveReaderViewportPreference(
  preference: ReaderViewportPreference,
  storage: PreferenceStorage | null = browserStorage(),
): void {
  if (!storage) return;

  try {
    storage.setItem(
      READER_VIEWPORT_PREFERENCE_KEY,
      JSON.stringify({
        fitMode: preference.fitMode,
        zoom: clampReaderZoom(preference.zoom),
      }),
    );
  } catch {
    // Reader presentation preferences are best-effort and must not break reading.
  }
}

function isPreferenceRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
