#!/usr/bin/env node
import { spawnSync } from "node:child_process";

const [kind, input, output, ...rest] = process.argv.slice(2);
const dryRun = rest.includes("--dry-run");
const atIndex = rest.indexOf("--at");
const at = atIndex >= 0 ? rest[atIndex + 1] : "00:00:01";

function usage(message) {
  if (message) console.error(message);
  console.error("usage: node scripts/media/derive.mjs <mp4|gif|still> <input> <output> [--at HH:MM:SS] [--dry-run]");
  process.exit(2);
}

if (!kind || !input || !output) usage();
if (!new Set(["mp4", "gif", "still"]).has(kind)) usage(`unsupported derivative: ${kind}`);
if (kind === "still" && (!at || at.startsWith("--"))) usage("--at requires a timestamp");

const ffmpeg = process.env.FFMPEG_BIN || "ffmpeg";
const common = ["-hide_banner", "-loglevel", "error", "-nostdin", "-y"];
let args;
if (kind === "mp4") {
  args = [
    ...common,
    "-i", input,
    "-map_metadata", "-1",
    "-an",
    "-c:v", "libx264",
    "-preset", "slow",
    "-crf", "18",
    "-pix_fmt", "yuv420p",
    "-movflags", "+faststart",
    "-metadata", "creation_time=1970-01-01T00:00:00Z",
    output,
  ];
} else if (kind === "gif") {
  args = [
    ...common,
    "-i", input,
    "-map_metadata", "-1",
    "-vf",
    "fps=12,scale='min(1200,iw)':-2:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=sierra2_4a",
    "-loop", "0",
    output,
  ];
} else {
  args = [
    ...common,
    "-ss", at,
    "-i", input,
    "-map_metadata", "-1",
    "-frames:v", "1",
    output,
  ];
}

function quote(value) {
  return /^[A-Za-z0-9_./:=+,-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\\''")}'`;
}

const command = [ffmpeg, ...args].map(quote).join(" ");
if (dryRun) {
  console.log(command);
  process.exit(0);
}

const version = spawnSync(ffmpeg, ["-version"], { encoding: "utf8" });
if (version.status !== 0) {
  console.error("ffmpeg is required for derivatives; set FFMPEG_BIN if it is not on PATH");
  process.exit(version.status ?? 1);
}
console.log(version.stdout.split("\n", 1)[0]);
console.log(command);
const result = spawnSync(ffmpeg, args, { stdio: "inherit" });
if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);
