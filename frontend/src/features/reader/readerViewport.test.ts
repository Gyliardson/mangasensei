import { describe, expect, it } from "vitest";

import {
  READER_ZOOM_MAX,
  READER_ZOOM_MIN,
  calculateReaderCanvasWidth,
  clampReaderZoom,
  isReaderFitMode,
} from "./readerViewport";

describe("reader viewport sizing", () => {
  const portrait = { width: 1600, height: 2400 };
  const landscape = { width: 2400, height: 1200 };
  const desktop = { width: 900, height: 900 };

  it("bounds a portrait page by viewport height in the comfortable desktop default", () => {
    const width = calculateReaderCanvasWidth(portrait, desktop, "comfortable", 100);

    expect(width).toBeLessThan(desktop.width);
    expect(width).toBeCloseTo(528, 0);
  });

  it("uses the available reader width as the comfortable mobile baseline", () => {
    expect(
      calculateReaderCanvasWidth(portrait, { width: 390, height: 500 }, "comfortable", 100),
    ).toBe(390);
    expect(
      calculateReaderCanvasWidth(landscape, { width: 390, height: 500 }, "comfortable", 100),
    ).toBe(390);
  });

  it("fits the complete page using both available width and height", () => {
    expect(calculateReaderCanvasWidth(portrait, { width: 900, height: 600 }, "page", 100)).toBeCloseTo(400);
    expect(calculateReaderCanvasWidth(landscape, { width: 900, height: 300 }, "page", 100)).toBeCloseTo(600);
  });

  it("uses the full reader column in fit-width mode for either orientation", () => {
    expect(calculateReaderCanvasWidth(portrait, desktop, "width", 100)).toBe(900);
    expect(calculateReaderCanvasWidth(landscape, desktop, "width", 100)).toBe(900);
  });

  it("applies zoom to the fitted baseline and clamps unsafe extremes", () => {
    expect(calculateReaderCanvasWidth(portrait, desktop, "width", 150)).toBe(1350);
    expect(calculateReaderCanvasWidth(portrait, desktop, "width", 500)).toBe(1800);
    expect(clampReaderZoom(10)).toBe(READER_ZOOM_MIN);
    expect(clampReaderZoom(500)).toBe(READER_ZOOM_MAX);
  });

  it("accepts only supported fit modes", () => {
    expect(isReaderFitMode("comfortable")).toBe(true);
    expect(isReaderFitMode("page")).toBe(true);
    expect(isReaderFitMode("width")).toBe(true);
    expect(isReaderFitMode("actual-size")).toBe(false);
    expect(isReaderFitMode(null)).toBe(false);
  });

  it("handles malformed zero dimensions without producing a non-finite width", () => {
    const width = calculateReaderCanvasWidth(
      { width: 0, height: 0 },
      { width: 0, height: 0 },
      "page",
      100,
    );

    expect(Number.isFinite(width)).toBe(true);
    expect(width).toBeGreaterThan(0);
  });
});
