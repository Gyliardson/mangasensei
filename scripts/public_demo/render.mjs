import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import crypto from "node:crypto";
import { chromium } from "playwright";

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "../..");
const corpus = path.join(root, "assets", "public-demo");
const sourceDir = path.join(corpus, "source");
const outputDir = path.join(corpus, "images");
const fontDir = process.env.MANGASENSEI_PUBLIC_DEMO_FONT_DIR
  ? path.resolve(process.env.MANGASENSEI_PUBLIC_DEMO_FONT_DIR)
  : path.join(root, "var", "public-demo", "fonts");
const fontsManifest = JSON.parse(
  await fs.readFile(path.join(corpus, "provenance", "fonts.json"), "utf8"),
);

function hash(buffer) {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

async function verifiedFont(cacheFile, expectedSha256) {
  const file = path.join(fontDir, cacheFile);
  const data = await fs.readFile(file).catch(() => null);
  if (!data) throw new Error(`missing required font ${file}; run fetch_fonts.py first`);
  const actual = hash(data);
  if (actual !== expectedSha256) {
    throw new Error(`font SHA-256 mismatch for ${file}: ${actual}`);
  }
  return file;
}

const byId = new Map(fontsManifest.fonts.map((font) => [font.id, font]));
const sansRegular = await verifiedFont(
  byId.get("noto-sans-cjk-regular").cacheFile,
  byId.get("noto-sans-cjk-regular").sha256,
);
const sansBold = await verifiedFont(
  byId.get("noto-sans-cjk-bold").cacheFile,
  byId.get("noto-sans-cjk-bold").sha256,
);
const serifRegular = await verifiedFont(
  byId.get("noto-serif-cjk-regular").cacheFile,
  byId.get("noto-serif-cjk-regular").sha256,
);
const serifBold = await verifiedFont(
  byId.get("noto-serif-cjk-bold").cacheFile,
  byId.get("noto-serif-cjk-bold").sha256,
);

function fontFaceCss() {
  const uri = (file) => pathToFileURL(file).href;
  return `
  <style id="mangasensei-pinned-fonts">
    @font-face { font-family: 'MangaSensei Sans v1'; src: url('${uri(sansRegular)}'); font-weight: 400; font-style: normal; }
    @font-face { font-family: 'MangaSensei Sans v1'; src: url('${uri(sansBold)}'); font-weight: 700; font-style: normal; }
    @font-face { font-family: 'MangaSensei Serif v1'; src: url('${uri(serifRegular)}'); font-weight: 400; font-style: normal; }
    @font-face { font-family: 'MangaSensei Serif v1'; src: url('${uri(serifBold)}'); font-weight: 700; font-style: normal; }
  </style>`;
}

await fs.mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  console.log(`Chromium ${browser.version()}`);
  const filenames = (await fs.readdir(sourceDir))
    .filter((name) => name.endsWith(".svg"))
    .sort();
  for (const filename of filenames) {
    const source = await fs.readFile(path.join(sourceDir, filename), "utf8");
    const injected = source.replace(/(<svg[^>]*>)/, `$1${fontFaceCss()}`);
    const temp = path.join(outputDir, `.${filename}`);
    await fs.writeFile(temp, injected, "utf8");
    const context = await browser.newContext({
      viewport: { width: 1440, height: 2048 },
      deviceScaleFactor: 1,
    });
    const page = await context.newPage();
    await page.goto(pathToFileURL(temp).href, { waitUntil: "load" });
    await page.evaluate(async () => {
      await document.fonts.ready;
      const expected = ["MangaSensei Sans v1", "MangaSensei Serif v1"];
      for (const family of expected) {
        if (!document.fonts.check(`40px "${family}"`, "日本語約束博士ロ口")) {
          throw new Error(`required pinned font did not load: ${family}`);
        }
      }
      const svg = document.querySelector("svg");
      if (!svg || svg.getAttribute("width") !== "1440" || svg.getAttribute("height") !== "2048") {
        throw new Error("source SVG dimensions are not canonical 1440x2048");
      }
    });
    const output = path.join(outputDir, filename.replace(/\.svg$/, ".png"));
    await page.locator("svg").screenshot({ path: output, animations: "disabled" });
    await context.close();
    await fs.rm(temp);
    console.log(`rendered ${output}`);
  }
} finally {
  await browser.close();
}
