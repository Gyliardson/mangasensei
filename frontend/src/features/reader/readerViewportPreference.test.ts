import { describe, expect, it, vi } from "vitest";

import {
  READER_VIEWPORT_PREFERENCE_KEY,
  loadReaderViewportPreference,
  saveReaderViewportPreference,
} from "./readerViewportPreference";

describe("reader viewport preference storage", () => {
  it("defaults safely for missing, malformed or unsupported values", () => {
    expect(loadReaderViewportPreference({ getItem: () => null, setItem: vi.fn() })).toEqual({
      fitMode: "comfortable",
      zoom: 100,
    });
    expect(loadReaderViewportPreference({ getItem: () => "not-json", setItem: vi.fn() })).toEqual({
      fitMode: "comfortable",
      zoom: 100,
    });
    expect(
      loadReaderViewportPreference({
        getItem: () => JSON.stringify({ fitMode: "actual-size", zoom: 125 }),
        setItem: vi.fn(),
      }),
    ).toEqual({ fitMode: "comfortable", zoom: 100 });
  });

  it("restores supported values and clamps stale zoom ranges", () => {
    expect(
      loadReaderViewportPreference({
        getItem: () => JSON.stringify({ fitMode: "page", zoom: 125 }),
        setItem: vi.fn(),
      }),
    ).toEqual({ fitMode: "page", zoom: 125 });
    expect(
      loadReaderViewportPreference({
        getItem: () => JSON.stringify({ fitMode: "width", zoom: 500 }),
        setItem: vi.fn(),
      }),
    ).toEqual({ fitMode: "width", zoom: 200 });
  });

  it("falls back when storage cannot be read", () => {
    expect(
      loadReaderViewportPreference({
        getItem: () => {
          throw new DOMException("blocked");
        },
        setItem: vi.fn(),
      }),
    ).toEqual({ fitMode: "comfortable", zoom: 100 });
  });

  it("persists normalized preferences and ignores write failures", () => {
    const setItem = vi.fn();
    saveReaderViewportPreference(
      { fitMode: "width", zoom: 250 },
      { getItem: vi.fn(), setItem },
    );
    expect(setItem).toHaveBeenCalledWith(
      READER_VIEWPORT_PREFERENCE_KEY,
      JSON.stringify({ fitMode: "width", zoom: 200 }),
    );

    expect(() =>
      saveReaderViewportPreference(
        { fitMode: "page", zoom: 100 },
        {
          getItem: vi.fn(),
          setItem: () => {
            throw new DOMException("quota exceeded");
          },
        },
      ),
    ).not.toThrow();
  });
});
