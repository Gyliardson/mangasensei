#!/usr/bin/env node
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const repoRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const catalogPath = path.join(repoRoot, "frontend/e2e/media/scenarios.json");
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

console.log(`media contract ok: ${ids.length} scenarios, deterministic paths, derivative dry-run`);
