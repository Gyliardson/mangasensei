import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

function dropTarget(): HTMLLabelElement {
  const target = screen.getByText("Selecionar imagem").closest("label");
  if (!(target instanceof HTMLLabelElement)) throw new Error("upload drop target not found");
  return target;
}

describe("upload drag and drop", () => {
  it("selects one dropped image through the same standalone upload state as the file picker", () => {
    render(<App />);
    const file = new File(["image"], "pagina.png", { type: "image/png" });

    fireEvent.drop(dropTarget(), { dataTransfer: { files: [file] } });

    expect(screen.getByText(/pagina\.png/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Analisar página" })).toBeEnabled();
  });

  it("preserves multiple-file drop order and exposes explicit reorder controls", () => {
    render(<App />);
    const first = new File(["one"], "z-uma.png", { type: "image/png" });
    const second = new File(["two"], "a-duas.png", { type: "image/png" });

    fireEvent.drop(dropTarget(), { dataTransfer: { files: [first, second] } });

    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("z-uma.png");
    expect(items[1]).toHaveTextContent("a-duas.png");
    expect(screen.getByRole("button", { name: "Analisar 2 páginas" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Mover z-uma.png para cima" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Mover z-uma.png para baixo" })).toBeEnabled();
  });
});
