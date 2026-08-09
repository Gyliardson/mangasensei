import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

describe("processing observation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("states that stopping observation does not cancel backend processing or retention", async () => {
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
                expiresAt: "2026-08-10T00:00:00Z",
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
          data: { status: "pending", error: null, resultAvailable: false },
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

    expect(await screen.findByRole("button", { name: "Parar de acompanhar" })).toBeVisible();
    expect(
      screen.getByText(
        "Isso interrompe apenas a espera nesta tela; a análise continuará e seguirá a exclusão automática de 24 horas.",
      ),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: "Cancelar" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Parar de acompanhar" }));

    expect(screen.getByRole("heading", { name: "Escolha uma página" })).toBeVisible();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:pending-image");
  });
});
