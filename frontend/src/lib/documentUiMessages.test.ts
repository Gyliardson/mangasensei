import { describe, expect, it } from "vitest";

import { documentMessagesFor } from "./documentUiMessages";

describe("document UI messages", () => {
  it("formats English selection, navigation, progress and statuses", () => {
    const messages = documentMessagesFor("en");

    expect(messages.selectedPages(1)).toBe("1 page selected");
    expect(messages.selectedPages(2)).toBe("2 pages selected");
    expect(messages.analyzePages(1)).toBe("Analyze page");
    expect(messages.analyzePages(3)).toBe("Analyze 3 pages");
    expect(messages.pageOf(2, 7)).toBe("Page 2 of 7");
    expect(messages.documentProgress(2, 7, 4, 1, 0)).toBe(
      "2 / 7 pages readable · 4 processing · 1 failed · 0 cancelled",
    );
    expect(messages.pageStatus(3, "processing_linguistics", false)).toBe(
      "Page 3: processing linguistics",
    );
    expect(messages.pageStatus(3, "failed", true)).toBe("Page 3: readable");
    expect(messages.movePageUp("three.png")).toBe("Move three.png earlier");
    expect(messages.movePageDown("three.png")).toBe("Move three.png later");
    expect(messages.removePage("three.png")).toBe("Remove three.png");
  });

  it("formats Brazilian Portuguese selection, navigation, progress and statuses", () => {
    const messages = documentMessagesFor("pt-BR");

    expect(messages.selectedPages(1)).toBe("1 página selecionada");
    expect(messages.selectedPages(2)).toBe("2 páginas selecionadas");
    expect(messages.analyzePages(1)).toBe("Analisar página");
    expect(messages.analyzePages(3)).toBe("Analisar 3 páginas");
    expect(messages.pageOf(2, 7)).toBe("Página 2 de 7");
    expect(messages.documentProgress(2, 7, 4, 1, 0)).toBe(
      "2 / 7 páginas disponíveis · 4 processando · 1 falharam · 0 canceladas",
    );
    expect(messages.pageStatus(3, "processing_ocr", false)).toBe("Página 3: processing ocr");
    expect(messages.pageStatus(3, "expired", true)).toBe("Página 3: disponível");
    expect(messages.movePageUp("tres.png")).toBe("Mover tres.png para cima");
    expect(messages.movePageDown("tres.png")).toBe("Mover tres.png para baixo");
    expect(messages.removePage("tres.png")).toBe("Remover tres.png");
  });

  it("exposes localized aggregate-limit and reload copy", () => {
    const en = documentMessagesFor("en");
    const ptBR = documentMessagesFor("pt-BR");

    expect(en.documentPageLimit).toContain("too many pages");
    expect(en.documentByteLimit).toContain("combined image size");
    expect(en.documentPixelLimit).toContain("combined image resolution");
    expect(en.documentReloadNote).toContain("never placed in the URL");
    expect(ptBR.documentPageLimit).toContain("páginas demais");
    expect(ptBR.documentByteLimit).toContain("tamanho combinado");
    expect(ptBR.documentPixelLimit).toContain("resolução combinada");
    expect(ptBR.documentReloadNote).toContain("nunca são colocados na URL");
  });
});
