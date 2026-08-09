import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { STUDY_LANGUAGE_PREFERENCE_KEY } from "./lib/studyLanguage";

function uploadData(studyLanguage: "pt-BR" | "en" = "pt-BR") {
  return {
    pageId: "page-001",
    jobId: "job-001",
    contentSha256: "a".repeat(64),
    width: 80,
    height: 120,
    mediaType: "image/png",
    expiresAt: "2026-08-09T00:00:00Z",
    studyLanguage,
    capabilities: {
      readPage: "read-page-token",
      readImage: "read-image-token",
      reprocessPage: "reprocess-token",
    },
  };
}

function studyPage(studyLanguage: "pt-BR" | "en" = "pt-BR") {
  const english = studyLanguage === "en";
  return {
    pageId: "page-001",
    status: "completed",
    resultAvailable: true,
    contentLanguage: "ja",
    studyLanguage,
    dictionaryLanguage: "en",
    expiresAt: "2026-08-09T00:00:00Z",
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
        translation: english ? "It is a cat." : "É um gato.",
        explanation: english ? "A polite nominal sentence." : "Frase nominal polida.",
        grammar: [english ? "polite copula" : "cópula polida"],
        vocabulary: [
          {
            id: "jmdict-1467640",
            surface: "猫",
            lemma: "猫",
            reading: "ネコ",
            meanings: ["cat"],
            source: "JMdict",
            jlpt: { level: "N5", official: false },
          },
        ],
      },
    ],
  };
}

describe("App", () => {
  afterEach(() => {
    window.localStorage.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("offers an accessible Japanese image upload in Brazilian Portuguese", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByRole("heading", { name: "Leia japonês no contexto" })).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
    expect(screen.getByText(/o conteúdo analisado continua japonês/i)).toBeVisible();
    const input = screen.getByLabelText("Imagem da página") as HTMLInputElement;
    const file = new File(["image"], "pagina.png", { type: "image/png" });
    await user.upload(input, file);

    expect(input.files?.[0]).toBe(file);
    expect(screen.getByText(/excluídos automaticamente após 24 horas/i)).toBeVisible();
  });

  it("restores a valid English preference and sends it explicitly on upload", async () => {
    window.localStorage.setItem(STUDY_LANGUAGE_PREFERENCE_KEY, "en");
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () =>
      Response.json(
        { success: false, data: null, error: { code: "fixture_stop", message: "fixture" } },
        { status: 422 },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
    await user.upload(
      screen.getByLabelText("Imagem da página"),
      new File(["image"], "page.png", { type: "image/png" }),
    );
    await user.click(screen.getByRole("button", { name: "Analisar página" }));

    const uploadCall = fetchMock.mock.calls.find(([input]) => String(input) === "/api/v1/pages");
    expect(uploadCall).toBeDefined();
    const form = uploadCall?.[1]?.body as FormData;
    expect(form.get("studyLanguage")).toBe("en");
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
          return Response.json({ success: true, data: uploadData(), error: null }, { status: 202 });
        }
        if (url.endsWith("/image")) {
          return new Response("image", { headers: { "Content-Type": "image/png" } });
        }
        if (url.endsWith("/status")) {
          return Response.json({
            success: true,
            data: { status: "completed", resultAvailable: true, error: null },
            error: null,
          });
        }
        if (url === "/api/v1/pages/page-001") {
          return Response.json({ success: true, data: studyPage(), error: null });
        }
        return new Response(null, { status: 404 });
      }),
    );
    render(<App />);
    const file = new File(["image"], "pagina.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Imagem da página"), file);

    await user.click(screen.getByRole("button", { name: "Analisar página" }));

    expect(await screen.findByRole("button", { name: /região 1: 猫です/i })).toBeVisible();
    expect(screen.getByText("É um gato.")).toHaveAttribute("lang", "pt-BR");
    expect(screen.getByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByText("ねこ", { selector: "rt" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Nova página" }));
    expect(screen.getByRole("heading", { name: "Escolha uma página" })).toBeVisible();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:protected-image");
  });

  it("reprocesses only the study language and replaces the page after completion", async () => {
    const user = userEvent.setup();
    let language: "pt-BR" | "en" = "pt-BR";
    let reprocessStarted = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/v1/pages" && init?.method === "POST") {
        return Response.json({ success: true, data: uploadData(), error: null }, { status: 202 });
      }
      if (url.endsWith("/image")) return new Response("image");
      if (url.endsWith("/reprocess")) {
        expect(init?.headers).toMatchObject({ "X-Page-Token": "reprocess-token" });
        expect(JSON.parse(String(init?.body))).toEqual({ studyLanguage: "en" });
        reprocessStarted = true;
        language = "en";
        return Response.json(
          {
            success: true,
            data: { jobId: "job-002", status: "pending", studyLanguage: "en", created: true },
            error: null,
          },
          { status: 202 },
        );
      }
      if (url.endsWith("/status")) {
        return Response.json({
          success: true,
          data: { status: "completed", resultAvailable: true, error: null },
          error: null,
        });
      }
      if (url === "/api/v1/pages/page-001") {
        return Response.json({ success: true, data: studyPage(language), error: null });
      }
      return new Response(null, { status: 404 });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:protected-image"),
      revokeObjectURL: vi.fn(),
    });
    render(<App />);
    await user.upload(
      screen.getByLabelText("Imagem da página"),
      new File(["image"], "page.png", { type: "image/png" }),
    );
    await user.click(screen.getByRole("button", { name: "Analisar página" }));
    expect(await screen.findByText("É um gato.")).toBeVisible();

    const selector = screen.getByRole("combobox", { name: "Idioma de estudo" });
    await user.selectOptions(selector, "en");

    await waitFor(() => expect(reprocessStarted).toBe(true));
    expect(await screen.findByText("It is a cat.")).toHaveAttribute("lang", "en");
    expect(screen.queryByText("É um gato.")).not.toBeInTheDocument();
    expect(screen.getByText("cat")).toHaveAttribute("lang", "en");
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("en");
    expect(window.localStorage.getItem(STUDY_LANGUAGE_PREFERENCE_KEY)).toBe("en");
  });

  it("keeps the completed result and rolls the preference back when language reprocessing fails", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:protected-image"),
      revokeObjectURL: vi.fn(),
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/pages") {
          return Response.json({ success: true, data: uploadData(), error: null }, { status: 202 });
        }
        if (url.endsWith("/image")) return new Response("image");
        if (url.endsWith("/reprocess")) {
          return Response.json(
            { success: false, data: null, error: { code: "processing_failed", message: "Falha controlada." } },
            { status: 409 },
          );
        }
        if (url.endsWith("/status")) {
          return Response.json({
            success: true,
            data: { status: "completed", resultAvailable: true, error: null },
            error: null,
          });
        }
        if (url === "/api/v1/pages/page-001") {
          return Response.json({ success: true, data: studyPage(), error: null });
        }
        return new Response(null, { status: 404 });
      }),
    );
    render(<App />);
    await user.upload(
      screen.getByLabelText("Imagem da página"),
      new File(["image"], "page.png", { type: "image/png" }),
    );
    await user.click(screen.getByRole("button", { name: "Analisar página" }));
    expect(await screen.findByText("É um gato.")).toBeVisible();

    await user.selectOptions(screen.getByRole("combobox", { name: "Idioma de estudo" }), "en");

    expect(await screen.findByRole("alert")).toHaveTextContent("Falha controlada.");
    expect(screen.getByText("É um gato.")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Idioma de estudo" })).toHaveValue("pt-BR");
    expect(window.localStorage.getItem(STUDY_LANGUAGE_PREFERENCE_KEY)).toBe("pt-BR");
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
            { success: true, data: uploadData(), error: null },
            { status: 202 },
          );
        }
        if (url.endsWith("/image")) return new Response("image");
        return Response.json({
          success: true,
          data: { status: "pending", resultAvailable: false, error: null },
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
