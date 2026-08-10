import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("allows duplicate names to coexist as separate logical selections", () => {
    render(<App />);
    const first = new File(["same"], "duplicada.png", { type: "image/png" });
    const second = new File(["same"], "duplicada.png", { type: "image/png" });

    fireEvent.drop(dropTarget(), { dataTransfer: { files: [first, second] } });

    expect(screen.getAllByText(/duplicada\.png/)).toHaveLength(2);
    expect(screen.getByText("2 páginas selecionadas")).toBeVisible();
  });

  it("supports keyboard-only reorder through the native move controls", async () => {
    const user = userEvent.setup();
    render(<App />);
    const first = new File(["one"], "primeira.png", { type: "image/png" });
    const second = new File(["two"], "segunda.png", { type: "image/png" });

    fireEvent.drop(dropTarget(), { dataTransfer: { files: [first, second] } });
    const moveUp = screen.getByRole("button", { name: "Mover segunda.png para cima" });
    moveUp.focus();
    await user.keyboard("{Enter}");

    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("segunda.png");
    expect(items[1]).toHaveTextContent("primeira.png");
  });

  it("removes individual pages and clears the remaining selection", async () => {
    const user = userEvent.setup();
    render(<App />);
    const first = new File(["one"], "um.png", { type: "image/png" });
    const second = new File(["two"], "dois.png", { type: "image/png" });
    const third = new File(["three"], "tres.png", { type: "image/png" });

    fireEvent.drop(dropTarget(), { dataTransfer: { files: [first, second, third] } });
    await user.click(screen.getByRole("button", { name: "Remover dois.png" }));

    const items = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("um.png");
    expect(items[1]).toHaveTextContent("tres.png");

    await user.click(screen.getByRole("button", { name: "Limpar seleção" }));
    expect(screen.queryByRole("list")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Analisar página" })).toBeDisabled();
  });
});
