import { expect, test } from "@playwright/test";

test("keeps the upload index as overlapping typography without a rectangular mask", async ({ page }) => {
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
    const beforeStyle = getComputedStyle(index, "::before");
    const afterStyle = getComputedStyle(index, "::after");
    const hasGeneratedContour = [beforeStyle, afterStyle].some(
      (style) => style.content !== "none" && style.content !== "normal" && style.content !== '""',
    );
    const hasContourTreatment =
      indexStyle.textShadow !== "none" ||
      indexStyle.filter !== "none" ||
      Number.parseFloat(indexStyle.webkitTextStrokeWidth || "0") > 0 ||
      hasGeneratedContour;

    return {
      workspaceLeft: workspaceRect.left,
      panelTop: panelRect.top,
      indexTop: indexRect.top,
      indexBottom: indexRect.bottom,
      indexHeight: indexRect.height,
      indexLeft: indexRect.left,
      indexRight: indexRect.right,
      indexBackground: indexStyle.backgroundColor,
      indexPaddingLeft: Number.parseFloat(indexStyle.paddingLeft),
      indexPaddingRight: Number.parseFloat(indexStyle.paddingRight),
      hasContourTreatment,
      previewLeft: previewRect.left,
      viewportWidth: window.innerWidth,
      documentWidth: document.documentElement.scrollWidth,
    };
  });

  expect(Math.abs(layout.previewLeft - layout.workspaceLeft)).toBeLessThanOrEqual(1);

  const overlapRatio = (layout.indexBottom - layout.panelTop) / layout.indexHeight;
  expect(overlapRatio).toBeGreaterThan(0.15);
  expect(overlapRatio).toBeLessThan(0.65);
  expect(layout.indexTop).toBeGreaterThanOrEqual(0);
  expect(layout.indexTop).toBeLessThan(layout.panelTop);

  expect(layout.indexBackground).toBe("rgba(0, 0, 0, 0)");
  expect(layout.indexPaddingLeft).toBeLessThanOrEqual(1);
  expect(layout.indexPaddingRight).toBeLessThanOrEqual(1);
  expect(layout.hasContourTreatment).toBe(true);

  expect(layout.indexLeft).toBeGreaterThanOrEqual(0);
  expect(layout.indexRight).toBeLessThanOrEqual(layout.viewportWidth);
  expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth);
});
