import { expect, test } from "@playwright/test";

test("selects a dropped image without replacing the keyboard-accessible file picker", async ({ page }) => {
  await page.goto("/");

  await page.locator(".file-drop").evaluate((target) => {
    const transfer = new DataTransfer();
    transfer.items.add(new File(["image"], "pagina-drop.png", { type: "image/png" }));
    target.dispatchEvent(
      new DragEvent("drop", {
        bubbles: true,
        cancelable: true,
        dataTransfer: transfer,
      }),
    );
  });

  await expect(page.getByText("pagina-drop.png")).toBeVisible();
  await expect(page.getByRole("button", { name: "Analisar página" })).toBeEnabled();
  await expect(page.getByLabel("Imagem da página")).toBeAttached();
});
