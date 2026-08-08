import { describe, expect, it } from "vitest";

import { toSvgBox } from "./overlay";

describe("toSvgBox", () => {
  it("maps normalized coordinates to a responsive SVG viewBox", () => {
    expect(
      toSvgBox({ x: 0.1, y: 0.2, width: 0.3, height: 0.25 }, { width: 1000, height: 2000 }),
    ).toEqual({ x: 100, y: 400, width: 300, height: 500 });
  });
});
