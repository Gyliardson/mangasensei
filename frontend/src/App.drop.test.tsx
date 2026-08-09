import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

function dropTarget(): HTMLLabelElement {
  const target = screen.getByText("Selecionar imagem").closest("label");
  if (!(target instanceof HTMLLabelElement)) throw new Error("upload drop target not found");
  return target;
}

describe("upload drag and drop", () => {
  it("selects one dropped image through the same upload state as the file picker", () => {
    render(<App />);
    const file = new File(["image"], "pagina.png", { type: "image/png" });

    fireEvent.drop(dropTarget(), { dataTransfer: { files: [file] } });

    expect(screen.getByText("pagina.png")).toBeVisible();
    expect(screen.getByRole("button", { name: "Analisar página" })).toBeEnabled();
  });

  it("rejects a multiple-file drop instead of silently choosing one", () => {
    render(<App />);
    const first = new File(["one"], "uma.png", { type: "image/png" });
    const second = new File(["two"], "duas.png", { type: "image/png" });

    fireEvent.drop(dropTarget(), { dataTransfer: { files: [first, second] } });

    expect(screen.getByRole("alert")).toHaveTextContent("Solte apenas uma imagem por vez.");
    expect(screen.getByRole("button", { name: "Analisar página" })).toBeDisabled();
    expect(screen.getByText("ou arraste o arquivo para cá")).toBeVisible();
  });
});
