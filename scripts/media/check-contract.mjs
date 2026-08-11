#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const repoRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const frontendRoot = path.join(repoRoot, "frontend");
const catalogPath = path.join(frontendRoot, "e2e/media/scenarios.json");
const catalog = JSON.parse(readFileSync(catalogPath, "utf8"));

assert.equal(catalog.schemaVersion, 1);
assert.equal(catalog.sourceId, "synthetic-v1");
assert.equal(catalog.captureClass, "presentation-fixture");
assert.ok(Array.isArray(catalog.scenarios));

const expected = new Set([
  "reader-desktop",
  "reader-mobile",
  "core-workflow",
  "multipage-partial",
  "multipage-navigation",
  "dictionary-language-switch",
]);
const ids = catalog.scenarios.map((scenario) => scenario.id);
assert.equal(new Set(ids).size, ids.length, "scenario ids must be unique");
assert.deepEqual(new Set(ids), expected, "scenario inventory drifted");

for (const scenario of catalog.scenarios) {
  assert.match(scenario.id, /^[a-z0-9][a-z0-9-]*$/);
  assert.ok(scenario.profiles.length > 0);
  assert.ok(scenario.profiles.every((profile) => profile === "desktop" || profile === "mobile"));
  assert.ok(scenario.formats.length > 0);
  assert.ok(scenario.formats.every((format) => format === "png" || format === "webm"));
  const examplePath = path.posix.join(catalog.sourceId, scenario.id, scenario.profiles[0], "master.png");
  assert.equal(examplePath.includes(".."), false);
  assert.equal(/token|secret|key/i.test(examplePath), false);
}

const derivativeDryRun = spawnSync(
  process.execPath,
  [
    path.join(repoRoot, "scripts/media/derive.mjs"),
    "mp4",
    "media-output/synthetic-v1/core-workflow/desktop/master.webm",
    "media-derivatives/core-workflow.mp4",
    "--dry-run",
  ],
  { cwd: repoRoot, encoding: "utf8" },
);
assert.equal(derivativeDryRun.status, 0, derivativeDryRun.stderr);
assert.match(derivativeDryRun.stdout, /ffmpeg/);
assert.match(derivativeDryRun.stdout, /libx264/);

function listPlaywrightTests(configFile) {
  const npx = process.platform === "win32" ? "npx.cmd" : "npx";
  const result = spawnSync(
    npx,
    ["--no-install", "playwright", "test", "--config", configFile, "--list"],
    { cwd: frontendRoot, encoding: "utf8" },
  );
  assert.equal(
    result.status,
    0,
    `Playwright discovery failed for ${configFile}:\n${result.stderr || result.stdout}`,
  );
  return result.stdout.replaceAll("\\", "/");
}

function discoveryRecords(output) {
  const records = [];
  for (const line of output.split(/\r?\n/)) {
    const match = line.match(/\[([^\]]+)\]\s+›\s+(.+?):\d+:\d+\s+›\s+(.+)$/);
    if (!match) continue;
    const [, project, testPath, title] = match;
    records.push({ project, testPath, title });
  }
  return records;
}

const mediaList = listPlaywrightTests("playwright.media.config.ts");
const mediaRecords = discoveryRecords(mediaList).map((record) => {
  const scenarioId = ids.find(
    (id) => record.title === id || record.title.startsWith(`${id} `),
  );
  assert.ok(
    scenarioId,
    `unexpected media story in dedicated discovery: ${record.project} ${record.testPath} ${record.title}`,
  );
  return { ...record, scenarioId };
});
assert.ok(mediaRecords.length > 0, `dedicated media config discovered no media stories:\n${mediaList}`);
assert.deepEqual(
  new Set(mediaRecords.map((record) => record.project)),
  new Set(["media-desktop", "media-mobile"]),
  "dedicated media project inventory drifted",
);
assert.deepEqual(
  new Set(mediaRecords.map((record) => record.scenarioId)),
  expected,
  "dedicated media config did not discover the expected stories",
);
for (const scenario of catalog.scenarios) {
  for (const profile of scenario.profiles) {
    const expectedProject = profile === "desktop" ? "media-desktop" : "media-mobile";
    assert.ok(
      mediaRecords.some(
        (record) => record.project === expectedProject && record.scenarioId === scenario.id,
      ),
      `${scenario.id} is missing from expected project ${expectedProject}`,
    );
  }
}

const defaultList = listPlaywrightTests("playwright.config.ts");
const defaultRecords = discoveryRecords(defaultList);
assert.ok(defaultRecords.length > 0, `default Playwright config discovered no tests:\n${defaultList}`);
const leakedMediaStories = defaultRecords.filter(
  (record) =>
    record.testPath.endsWith("e2e/media/capture.spec.ts") ||
    record.testPath.endsWith("media/capture.spec.ts"),
);
assert.deepEqual(
  leakedMediaStories,
  [],
  `default Playwright config discovered media stories:\n${leakedMediaStories
    .map((record) => `${record.project} ${record.testPath} ${record.title}`)
    .join("\n")}`,
);

console.log(
  `media contract ok: ${ids.length} scenarios, deterministic paths, derivative dry-run, discovery boundaries`,
);
