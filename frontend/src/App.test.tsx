import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("offers an accessible Japanese image upload in Brazilian Portuguese", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole("heading", { name: "Leia japonês no contexto" })).toBeVisible();
    const input = screen.getByLabelText("Imagem da página") as HTMLInputElement;
    const file = new File(["image"], "pagina.png", { type: "image/png" });
    await user.upload(input, file);

    expect(input.files?.[0]).toBe(file);
    expect(screen.getByText(/excluídos automaticamente após 24 horas/i)).toBeVisible();
  });

  it("has no automated accessibility violations in the upload state", async () => {
    const { container } = render(<App />);

    const results = await axe.run(container, {
      rules: { "color-contrast": { enabled: false } },
    });

    expect(results.violations).toEqual([]);
  });

  it("uploads, polls and renders a clickable study region without external HTML", async () => {
    const user = userEvent.setup();
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:protected-image"),
      revokeObjectURL,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/pages") {
          return Response.json(
            {
              success: true,
              data: {
                pageId: "page-001",
                jobId: "job-001",
                contentSha256: "a".repeat(64),
                width: 80,
                height: 120,
                mediaType: "image/png",
                expiresAt: "2026-08-08T00:00:00Z",
                capabilities: {
                  readPage: "read-page-token",
                  readImage: "read-image-token",
                  reprocessPage: "reprocess-token",
                },
              },
              error: null,
            },
            { status: 202 },
          );
        }
        if (url.endsWith("/image")) {
          return new Response("image", {
            headers: { "Content-Type": "image/png" },
          });
        }
        if (url.endsWith("/status")) {
          return Response.json({
            success: true,
            data: { status: "completed", error: null },
            error: null,
          });
        }
        if (url === "/api/v1/pages/page-001") {
          return Response.json({
            success: true,
            data: {
              pageId: "page-001",
              status: "completed",
              expiresAt: "2026-08-08T00:00:00Z",
              imageUrl: "/api/v1/pages/page-001/image",
              dimensions: { width: 80, height: 120 },
              ocr: { detector: "default", recognizer: "48px", upstreamCommit: "95227a2" },
              error: null,
              regions: [
                {
                  id: "region-001",
                  text: "猫です",
                  rawText: "猫です",
                  correctedText: null,
                  bbox: { x: 10, y: 20, width: 40, height: 60 },
                  normalizedBbox: { x: 0.125, y: 0.1667, width: 0.5, height: 0.5 },
                  polygon: [[10, 20], [50, 20], [50, 80], [10, 80]],
                  angle: 0,
                  confidence: 0.97,
                  readingOrder: 0,
                  tokens: [
                    {
                      surface: "猫",
                      lemma: "猫",
                      reading: "ネコ",
                      partOfSpeech: "名詞",
                      dictionaryId: "jmdict-1467640",
                    },
                  ],
                  translation: "É um gato.",
                  explanation: "Frase nominal polida.",
                  grammar: ["です"],
                  vocabulary: [
                    {
                      id: "jmdict-1467640",
                      surface: "猫",
                      lemma: "猫",
                      reading: "ネコ",
                      meanings: ["gato"],
                      source: "JMdict",
                      jlpt: { level: "N5", official: false },
                    },
                  ],
                },
              ],
            },
            error: null,
          });
        }
        return new Response(null, { status: 404 });
      }),
    );
    render(<App />);
    const file = new File(["image"], "pagina.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Imagem da página"), file);

    await user.click(screen.getByRole("button", { name: "Analisar página" }));

    expect(await screen.findByRole("button", { name: /região 1: 猫です/i })).toBeVisible();
    expect(screen.getByText("É um gato.")).toBeVisible();
    expect(screen.getByText("ねこ", { selector: "rt" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Nova página" }));
    expect(screen.getByRole("heading", { name: "Escolha uma página" })).toBeVisible();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:protected-image");
  });

  it("requires a file even when the form is submitted programmatically", () => {
    render(<App />);
    const submit = screen.getByRole("button", { name: "Analisar página" });

    fireEvent.submit(submit.closest("form") as HTMLFormElement);

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Selecione uma imagem antes de continuar.",
    );
  });

  it("shows the sanitized API message when upload is rejected", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          {
            success: false,
            data: null,
            error: { code: "invalid_image", message: "Imagem recusada." },
          },
          { status: 422 },
        ),
      ),
    );
    render(<App />);
    await user.upload(
      screen.getByLabelText("Imagem da página"),
      new File(["image"], "page.png", { type: "image/png" }),
    );

    await user.click(screen.getByRole("button", { name: "Analisar página" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Imagem recusada.");
  });

  it("uses a generic message for unexpected client failures", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("network detail"))));
    render(<App />);
    await user.upload(
      screen.getByLabelText("Imagem da página"),
      new File(["image"], "page.png", { type: "image/png" }),
    );

    await user.click(screen.getByRole("button", { name: "Analisar página" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "O processamento não pôde ser concluído.",
    );
  });

  it("stops observing the queued job without claiming to cancel backend processing", async () => {
    const user = userEvent.setup();
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:pending-image"),
      revokeObjectURL,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/pages") {
          return Response.json(
            {
              success: true,
              data: {
                pageId: "page-pending",
                jobId: "job-pending",
                contentSha256: "a".repeat(64),
                width: 80,
                height: 120,
                mediaType: "image/png",
                expiresAt: "2026-08-09T00:00:00Z",
                capabilities: {
                  readPage: "read-page-token",
                  readImage: "read-image-token",
                  reprocessPage: "reprocess-token",
                },
              },
              error: null,
            },
            { status: 202 },
          );
        }
        if (url.endsWith("/image")) return new Response("image");
        return Response.json({
          success: true,
          data: { status: "pending", error: null },
          error: null,
        });
      }),
    );
    render(<App />);
    await user.upload(
      screen.getByLabelText("Imagem da página"),
      new File(["image"], "page.png", { type: "image/png" }),
    );
    await user.click(screen.getByRole("button", { name: "Analisar página" }));

    expect(await screen.findByRole("button", { name: "Aguardando worker" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Parar de acompanhar" })).toBeVisible();
    expect(
      screen.getByText(
        "Parar de acompanhar interrompe apenas a espera nesta tela; a análise pode continuar. Originais e resultados são excluídos automaticamente após 24 horas.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancelar" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Parar de acompanhar" }));

    expect(screen.getByRole("button", { name: "Analisar página" })).toBeDisabled();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:pending-image");
  });
});
