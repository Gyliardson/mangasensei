export const DEFAULT_FURIGANA_MODE = "hiragana";

export type FuriganaMode = "hiragana" | "katakana" | "hidden";

export function isFuriganaMode(value: unknown): value is FuriganaMode {
  return value === "hiragana" || value === "katakana" || value === "hidden";
}

export function toHiragana(value: string): string {
  return Array.from(value, (character) => {
    const codePoint = character.codePointAt(0);
    if (codePoint !== undefined && codePoint >= 0x30a1 && codePoint <= 0x30f6) {
      return String.fromCodePoint(codePoint - 0x60);
    }
    return character;
  }).join("");
}

export function furiganaReading(
  surface: string,
  reading: string | null,
  mode: FuriganaMode = DEFAULT_FURIGANA_MODE,
): string | null {
  if (!reading || mode === "hidden") return null;

  const normalizedSurface = toHiragana(surface);
  const normalizedReading = toHiragana(reading);
  if (normalizedSurface === normalizedReading) return null;

  return mode === "katakana" ? reading : normalizedReading;
}
