import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const corpus = path.join(root, "assets", "reading-order-v2", "heldout-v1");
const sourceDir = path.join(corpus, "source");
const outputDir = path.join(corpus, "images");
const pageIds = Array.from({ length: 16 }, (_, index) => `H${String(index + 1).padStart(2, "0")}`);

await fs.mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  const discovered = (await fs.readdir(sourceDir)).filter((name) => name.endsWith(".svg")).sort();
  const expected = pageIds.map((id) => `${id}.svg`);
  if (JSON.stringify(discovered) !== JSON.stringify(expected)) {
    throw new Error("held-out render requires exactly source/H01.svg ... source/H16.svg");
  }
  for (const pageId of pageIds) {
    const sourcePath = path.join(sourceDir, `${pageId}.svg`);
    const source = await fs.readFile(sourcePath, "utf8");
    if (/https?:\/\//i.test(source) || /<(?:image|foreignObject)\b/i.test(source)) {
      throw new Error(`${pageId}: external/image/foreignObject content is forbidden`);
    }
    const temporary = path.join(outputDir, `.${pageId}.svg`);
    await fs.writeFile(temporary, source, "utf8");
    const context = await browser.newContext({ viewport: { width: 1440, height: 2048 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    await page.goto(pathToFileURL(temporary).href, { waitUntil: "load" });
    await page.evaluate((expectedPageId) => {
      const svg = document.querySelector("svg");
      if (!svg || svg.getAttribute("width") !== "1440" || svg.getAttribute("height") !== "2048") {
        throw new Error("source SVG dimensions must be exactly 1440x2048");
      }
      if (svg.getAttribute("data-page-id") !== expectedPageId) {
        throw new Error("source SVG data-page-id does not match filename");
      }
      if (document.querySelector("text")) {
        throw new Error("Reading Order v2 held-out source must not contain semantic text nodes");
      }
    }, pageId);
    await page.locator("svg").screenshot({
      path: path.join(outputDir, `${pageId}.png`),
      animations: "disabled",
    });
    await context.close();
    await fs.rm(temporary);
  }
} finally {
  await browser.close();
}
