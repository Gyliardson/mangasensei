import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import { chromium, type Browser, type Page, type TestInfo } from "@playwright/test";

export const MEDIA_SOURCE_ID = "synthetic-v1";
export const MEDIA_CAPTURE_CLASS = "presentation-fixture" as const;
export const MEDIA_FIXTURE_SECRETS = [
  "media-fixture-read-page-token",
  "media-fixture-read-image-token",
  "media-fixture-reprocess-token",
  "media-fixture-read-document-token",
  "media-fixture-read-document-image-token",
  "media-fixture-reprocess-document-token",
] as const;

export interface MediaScenario {
  readonly id: string;
  readonly profiles: readonly ("desktop" | "mobile")[];
  readonly formats: readonly ("png" | "webm")[];
  readonly state: string;
}

interface ScenarioCatalog {
  readonly schemaVersion: number;
  readonly sourceId: string;
  readonly captureClass: string;
  readonly scenarios: readonly MediaScenario[];
}

interface CapturedArtifact {
  readonly kind: "screenshot" | "screencast";
  readonly format: "png" | "webm";
  readonly path: string;
  readonly sha256: string;
  readonly bytes: number;
}

const repoRoot = path.resolve(fileURLToPath(new URL("../../../", import.meta.url)));
const frontendPackagePath = fileURLToPath(new URL("../../package.json", import.meta.url));
const catalogPath = fileURLToPath(new URL("./scenarios.json", import.meta.url));

export const MEDIA_PROFILES = {
  desktop: {
    projectName: "media-desktop",
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
    isMobile: false,
    hasTouch: false,
  },
  mobile: {
    projectName: "media-mobile",
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
  },
} as const;

function sha256(buffer: Buffer): string {
  return createHash("sha256").update(buffer).digest("hex");
}

function normalizedRelativePath(root: string, target: string): string {
  return path.relative(root, target).split(path.sep).join("/");
}

function safeSegment(value: string, label: string): string {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(value)) {
    throw new Error(`${label} must contain only lowercase letters, digits and hyphens`);
  }
  return value;
}

export async function loadScenarioCatalog(): Promise<ScenarioCatalog> {
  const raw = await readFile(catalogPath, "utf8");
  const parsed = JSON.parse(raw) as ScenarioCatalog;
  if (parsed.schemaVersion !== 1) throw new Error("unsupported media scenario catalog version");
  if (parsed.sourceId !== MEDIA_SOURCE_ID) throw new Error("scenario catalog sourceId mismatch");
  if (parsed.captureClass !== MEDIA_CAPTURE_CLASS) throw new Error("scenario catalog captureClass mismatch");
  return parsed;
}

export function profileForProject(projectName: string): "desktop" | "mobile" {
  if (projectName === MEDIA_PROFILES.desktop.projectName) return "desktop";
  if (projectName === MEDIA_PROFILES.mobile.projectName) return "mobile";
  throw new Error(`unknown media project: ${projectName}`);
}

export function mediaOutputRoot(): string {
  const configured = process.env.MANGASENSEI_MEDIA_OUTPUT;
  if (!configured) return path.join(repoRoot, "media-output");
  return path.isAbsolute(configured) ? configured : path.resolve(repoRoot, configured);
}

export function artifactDirectory(scenarioId: string, profile: "desktop" | "mobile"): string {
  const sourceId = safeSegment(process.env.MANGASENSEI_MEDIA_SOURCE ?? MEDIA_SOURCE_ID, "source id");
  if (sourceId !== MEDIA_SOURCE_ID) {
    throw new Error(
      `unsupported media source ${sourceId}; add a reviewed source adapter after the public corpus is frozen`,
    );
  }
  return path.join(mediaOutputRoot(), sourceId, safeSegment(scenarioId, "scenario id"), profile);
}

export async function stabilizePage(page: Page): Promise<void> {
  await page.emulateMedia({ colorScheme: "light", reducedMotion: "reduce" });
  await page.addStyleTag({
    content: `
      html { scroll-behavior: auto !important; }
      *, *::before, *::after {
        animation-duration: 0s !important;
        animation-delay: 0s !important;
        transition-duration: 0s !important;
        transition-delay: 0s !important;
        caret-color: transparent !important;
      }
      body, button, input, select, textarea {
        font-family: Arial, "Noto Sans CJK JP", "Noto Sans JP", sans-serif !important;
      }
    `,
  });
  await page.evaluate(async () => {
    await document.fonts.ready;
    (document.activeElement as HTMLElement | null)?.blur();
    window.scrollTo(0, 0);
  });
}

export async function captureScreenshot(
  page: Page,
  scenarioId: string,
  profile: "desktop" | "mobile",
): Promise<CapturedArtifact> {
  const directory = artifactDirectory(scenarioId, profile);
  await mkdir(directory, { recursive: true });
  const outputPath = path.join(directory, "master.png");
  await stabilizePage(page);
  const buffer = await page.screenshot({
    path: outputPath,
    fullPage: true,
    animations: "disabled",
    caret: "hide",
    scale: "css",
    type: "png",
  });
  assertNoFixtureSecret(buffer);
  return {
    kind: "screenshot",
    format: "png",
    path: normalizedRelativePath(mediaOutputRoot(), outputPath),
    sha256: sha256(buffer),
    bytes: buffer.byteLength,
  };
}

export async function startScreencast(
  page: Page,
  scenarioId: string,
  profile: "desktop" | "mobile",
): Promise<string> {
  const directory = artifactDirectory(scenarioId, profile);
  await mkdir(directory, { recursive: true });
  const outputPath = path.join(directory, "master.webm");
  const viewport = MEDIA_PROFILES[profile].viewport;
  await page.screencast.start({ path: outputPath, size: viewport, quality: 90 });
  return outputPath;
}

export async function stopScreencast(page: Page, outputPath: string): Promise<CapturedArtifact> {
  await page.screencast.stop();
  const buffer = await readFile(outputPath);
  assertNoFixtureSecret(buffer);
  return {
    kind: "screencast",
    format: "webm",
    path: normalizedRelativePath(mediaOutputRoot(), outputPath),
    sha256: sha256(buffer),
    bytes: buffer.byteLength,
  };
}

function gitSha(): string {
  const result = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: repoRoot,
    encoding: "utf8",
  });
  if (result.status !== 0) {
    throw new Error(`unable to resolve repository SHA: ${result.stderr.trim()}`);
  }
  return result.stdout.trim();
}

function playwrightVersion(packageJson: { readonly devDependencies?: Record<string, string> }): string {
  const version = packageJson.devDependencies?.["@playwright/test"];
  if (!version) throw new Error("@playwright/test is not pinned in frontend/package.json");
  return version;
}

function browserRevision(): string {
  const executablePath = chromium.executablePath();
  const match = executablePath.match(/(?:chromium|chromium_headless_shell)-(\d+)/);
  return match?.[1] ?? "unknown";
}

export async function writeAndVerifyProvenance(options: {
  readonly browser: Browser;
  readonly page: Page;
  readonly testInfo: TestInfo;
  readonly scenario: MediaScenario;
  readonly artifacts: readonly CapturedArtifact[];
}): Promise<string> {
  const { browser, page, testInfo, scenario, artifacts } = options;
  const profile = profileForProject(testInfo.project.name);
  const packageJson = JSON.parse(await readFile(frontendPackagePath, "utf8")) as {
    readonly devDependencies?: Record<string, string>;
  };
  const fontState = await page.evaluate(() => ({
    status: document.fonts.status,
    bodyFontFamily: getComputedStyle(document.body).fontFamily,
  }));
  const manifest = {
    schemaVersion: 1,
    captureClass: MEDIA_CAPTURE_CLASS,
    evidenceClaim: "deterministic presentation fixture; not real OCR or benchmark evidence",
    repository: {
      name: "Gyliardson/mangasensei",
      sha: gitSha(),
    },
    toolchain: {
      playwright: playwrightVersion(packageJson),
      browser: {
        engine: "chromium",
        version: browser.version(),
        bundledRevision: browserRevision(),
        headless: true,
      },
      runtime: {
        node: process.version,
        platform: process.platform,
        arch: process.arch,
      },
    },
    scenario: {
      id: scenario.id,
      state: scenario.state,
      sourceId: MEDIA_SOURCE_ID,
      profile,
      viewport: MEDIA_PROFILES[profile].viewport,
      deviceScaleFactor: MEDIA_PROFILES[profile].deviceScaleFactor,
      locale: "en-US",
      timezoneId: "UTC",
      colorScheme: "light",
      reducedMotion: "reduce",
      fontState,
    },
    artifacts,
  };
  const directory = artifactDirectory(scenario.id, profile);
  const manifestPath = path.join(directory, "provenance.json");
  const serialized = `${JSON.stringify(manifest, null, 2)}\n`;
  assertNoFixtureSecret(Buffer.from(serialized));
  await writeFile(manifestPath, serialized, "utf8");

  const parsed = JSON.parse(await readFile(manifestPath, "utf8")) as typeof manifest;
  if (parsed.schemaVersion !== 1) throw new Error("invalid provenance schemaVersion");
  if (parsed.repository.sha.length !== 40) throw new Error("invalid repository SHA in provenance");
  if (parsed.scenario.id !== scenario.id) throw new Error("provenance scenario mismatch");
  if (parsed.artifacts.length !== artifacts.length) throw new Error("provenance artifact count mismatch");
  for (const artifact of parsed.artifacts) {
    const artifactPath = path.join(mediaOutputRoot(), artifact.path);
    const buffer = await readFile(artifactPath);
    if (sha256(buffer) !== artifact.sha256) throw new Error(`artifact hash mismatch: ${artifact.path}`);
    if (buffer.byteLength !== artifact.bytes) throw new Error(`artifact size mismatch: ${artifact.path}`);
    assertNoFixtureSecret(buffer);
  }
  return manifestPath;
}

export function assertNoFixtureSecret(buffer: Buffer): void {
  const asText = buffer.toString("utf8");
  for (const secret of MEDIA_FIXTURE_SECRETS) {
    if (asText.includes(secret)) throw new Error("fixture capability token leaked into media output");
  }
}
