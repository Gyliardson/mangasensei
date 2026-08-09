import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { APPLICATION_VERSION, versionLabel } from "./version";

describe("application version presentation", () => {
  it("renders the synchronized frontend package version in the footer", () => {
    render(<App />);

    expect(screen.getByText(versionLabel(APPLICATION_VERSION))).toBeVisible();
  });

  it("formats a synthetic release version without assuming the current literal", () => {
    expect(versionLabel("9.8.7")).toBe("Versão 9.8.7");
  });
});
