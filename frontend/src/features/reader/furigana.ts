export function toHiragana(value: string): string {
  return Array.from(value, (character) => {
    const codePoint = character.codePointAt(0);
    if (codePoint !== undefined && codePoint >= 0x30a1 && codePoint <= 0x30f6) {
      return String.fromCodePoint(codePoint - 0x60);
    }
    return character;
  }).join("");
}

export function furiganaReading(surface: string, reading: string | null): string | null {
  if (!reading) return null;

  const presentedReading = toHiragana(reading);
  return toHiragana(surface) === presentedReading ? null : presentedReading;
}
