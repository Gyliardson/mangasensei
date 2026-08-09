import { expect, test } from "@playwright/test";

test("keeps the upload index masked and next-step content on the workspace axis", async ({ page }) => {
  await page.goto("/");

  const layout = await page.evaluate(() => {
    const workspace = document.querySelector<HTMLElement>(".workspace");
    const panel = document.querySelector<HTMLElement>(".upload-panel");
    const index = document.querySelector<HTMLElement>(".section-index");
    const preview = document.querySelector<HTMLElement>(".study-preview");
    if (!workspace || !panel || !index || !preview) throw new Error("upload layout missing");

    const workspaceRect = workspace.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const indexRect = index.getBoundingClientRect();
    const previewRect = preview.getBoundingClientRect();
    const indexStyle = getComputedStyle(index);

    return {
      workspaceLeft: workspaceRect.left,
      panelTop: panelRect.top,
      indexTop: indexRect.top,
      indexBottom: indexRect.bottom,
      indexLeft: indexRect.left,
      indexRight: indexRect.right,
      indexBackground: indexStyle.backgroundColor,
      indexZ: Number(indexStyle.zIndex),
      previewLeft: previewRect.left,
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
    };
  });

  expect(Math.abs(layout.previewLeft - layout.workspaceLeft)).toBeLessThanOrEqual(1);
  expect(layout.indexTop).toBeLessThan(layout.panelTop);
  expect(layout.indexBottom).toBeGreaterThan(layout.panelTop);
  expect(layout.indexBackground).not.toBe("rgba(0, 0, 0, 0)");
  expect(layout.indexZ).toBeGreaterThanOrEqual(1);
  expect(layout.indexLeft).toBeGreaterThanOrEqual(0);
  expect(layout.indexRight).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
});
