// Compare the viewer's CPU reference against real htpp output.
//
//   pnpm htpp-parity                 # htpp on PATH
//   HTPP="wsl htpp" pnpm htpp-parity # htpp inside WSL; paths are translated
//
// For every fixture it runs htpp in image mode (XYZ fixtures only) and in
// map mode for each of the 8 components across several palettes, with both
// automatic and explicit ranges, then diffs htpp's PPM against
// src/render/parity.ts's expectedPixels. The GPU is checked against that same
// reference by /parity.html, so passing both means the viewer matches htpp.
// It also compares one gnuplot script per fixture line by line.
//
// Exit code: 0 when everything matches within one 8-bit step, 1 on any
// mismatch, 2 when htpp can't be run.

import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { paletteByName, type Palette } from "../src/color/palettes";
import { derivedValues } from "../src/format/derived";
import { loadFromBytes, type LoadedImage } from "../src/format/loadedImage";
import { gnuplotChunks, parsePPM } from "../src/export/formats";
import { comparePixels, expectedPixels } from "../src/render/parity";
import { DEFAULT_RANGE, rangeOf } from "../src/render/range";
import { NO_COMPARE, type RenderSettings } from "../src/render/settings";
import type { Pair } from "../src/format/htrdrImage";

const TOLERANCE = 1;
const FIXTURE_DIR = resolve(import.meta.dirname, "..", "fixtures");
const PALETTES = ["inferno", "viridis", "magma", "plasma", "moreland", "jet", "blues", "rdbu", "set1", "ylorrd", "spectral", "greys"];

const command = (process.env.HTPP ?? "htpp").trim().split(/\s+/);
const viaWsl = /^wsl(\.exe)?$/i.test(command[0]!);

/** D:\a\b → /mnt/d/a/b when htpp runs inside WSL. */
function hostPath(p: string): string {
  if (!viaWsl) return p;
  const m = /^([A-Za-z]):[\\/](.*)$/.exec(p);
  return m ? `/mnt/${m[1]!.toLowerCase()}/${m[2]!.replace(/\\/g, "/")}` : p.replace(/\\/g, "/");
}

function htpp(args: string[]): { ok: boolean; stderr: string } {
  const r = spawnSync(command[0]!, [...command.slice(1), ...args], { encoding: "utf8" });
  if (r.error) return { ok: false, stderr: r.error.message };
  return { ok: r.status === 0, stderr: (r.stderr ?? "").trim() };
}

interface Case {
  label: string;
  args: string[];
  settings: RenderSettings;
  palette: Palette;
}

const base: RenderSettings = {
  mode: "map",
  exposure: 1,
  white: 1,
  pair: 0,
  stat: "value",
  range: [0, 0],
  log: false,
  compare: NO_COMPARE,
};

function casesFor(img: LoadedImage): Case[] {
  const out: Case[] = [];
  if (img.layout === "xyz") {
    for (const exposure of [1, 0.25, 4]) {
      for (const white of [null, img.stats.autoWhite * 2]) {
        const opts = [`exposure=${exposure}`, ...(white === null ? [] : [`white=${white}`])];
        out.push({
          label: `-i ${opts.join(":")}`,
          args: ["-i", opts.join(":")],
          settings: { ...base, mode: "image", exposure, white: white ?? img.stats.autoWhite },
          palette: paletteByName("gray"),
        });
      }
    }
  }
  let p = 0;
  for (let k = 0; k < 8; k++) {
    const pair = (k >> 1) as Pair;
    const stat = k & 1 ? "sigma" : "value";
    const values = derivedValues(img, pair, stat);
    for (const explicit of [false, true]) {
      const range = rangeOf(values, explicit ? { ...DEFAULT_RANGE, mode: "percentile", low: 5, high: 95 } : DEFAULT_RANGE);
      // A zero-width range is htpp's all-black case, and an explicit one would
      // be rejected (htpp needs min < max), so only test it automatically.
      if (explicit && !(range[0] < range[1])) continue;
      const palette = PALETTES[p++ % PALETTES.length]!;
      const opts = [`pixcpnt=${k}`, `palette=${palette}`, ...(explicit ? [`range=${range[0]},${range[1]}`] : [])];
      out.push({
        label: `-m ${opts.join(":")}`,
        args: ["-m", opts.join(":")],
        settings: { ...base, pair, stat, range },
        palette: paletteByName(palette),
      });
    }
  }
  return out;
}

/** Compare our gnuplot script with htpp's, allowing palette colors to differ by 1/255. */
function compareGnuplot(ours: string, theirs: string): string | null {
  // Ours starts with a comment line htpp doesn't write.
  const a = ours.split("\n").slice(1);
  const b = theirs.split("\n");
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i++) {
    if (a[i] === b[i]) continue;
    // Palette entries end in ",\" or ")"; keep those out of the blue value.
    const entry = /^ {2}(\d+) (\S+) (\S+) ([^\s,)\\]+)/;
    const pa = entry.exec(a[i] ?? "");
    const pb = entry.exec(b[i] ?? "");
    if (pa && pb && pa[1] === pb[1] && [2, 3, 4].every((j) => Math.abs(Number(pa[j]) - Number(pb[j])) <= 1 / 255 + 1e-9)) continue;
    return `line ${i + 1}: viewer "${a[i] ?? "<end>"}" vs htpp "${b[i] ?? "<end>"}"`;
  }
  return null;
}

function main(): number {
  const version = htpp(["-V"]);
  if (!version.ok) {
    console.error(`Can't run htpp with "${command.join(" ")}": ${version.stderr || "unknown error"}`);
    console.error('Install htpp, or point HTPP at it, e.g. HTPP="wsl htpp" pnpm htpp-parity');
    return 2;
  }

  const work = mkdtempSync(join(tmpdir(), "htpp-parity-"));
  const output = join(work, "out.ppm");
  let total = 0;
  let failures = 0;
  try {
    for (const file of readdirSync(FIXTURE_DIR).filter((f) => f.endsWith(".txt")).sort()) {
      const input = join(FIXTURE_DIR, file);
      const img = loadFromBytes(readFileSync(input));
      for (const c of casesFor(img)) {
        total++;
        const run = htpp(["-f", ...c.args, "-o", hostPath(output), hostPath(input)]);
        if (!run.ok) {
          failures++;
          console.log(`FAIL ${file} ${c.label}: htpp failed: ${run.stderr}`);
          continue;
        }
        const ppm = parsePPM(readFileSync(output, "utf8"));
        const result = comparePixels(expectedPixels(img, c.settings, { palette: c.palette }), ppm.rgba, TOLERANCE);
        const pass = result.mismatches === 0;
        if (!pass) failures++;
        const worst = result.worst ? ` worst #${result.worst.index}: viewer ${result.worst.expected} vs htpp ${result.worst.actual}` : "";
        console.log(`${pass ? "ok  " : "FAIL"} ${file} ${c.label}: max Δ ${result.maxDiff}, ${result.mismatches} pixels over${pass ? "" : worst}`);
      }

      // One gnuplot script per fixture: the first mean whose range isn't empty
      // (shortwave images leave pair 0 at zero), with that range given.
      const pair = ([0, 1, 2, 3] as Pair[]).find((p) => {
        const r = rangeOf(derivedValues(img, p, "value"), DEFAULT_RANGE);
        return r[0] < r[1];
      });
      if (pair === undefined) {
        console.log(`skip ${file} gnuplot script: every component is constant`);
        continue;
      }
      total++;
      const values = derivedValues(img, pair, "value");
      const range = rangeOf(values, DEFAULT_RANGE);
      const gp = join(work, "out.gp");
      const opts = `pixcpnt=${pair * 2}:palette=inferno:range=${range[0]},${range[1]}:gnuplot`;
      const run = htpp(["-f", "-m", opts, "-o", hostPath(gp), hostPath(input)]);
      const ours = gnuplotChunks({ width: img.width, height: img.height, values, palette: paletteByName("inferno"), range, log: false, title: "" }).join("");
      const problem = run.ok ? compareGnuplot(ours, readFileSync(gp, "utf8")) : `htpp failed: ${run.stderr}`;
      if (problem) failures++;
      console.log(`${problem ? "FAIL" : "ok  "} ${file} gnuplot script${problem ? `: ${problem}` : ""}`);
      if (process.argv.includes("--keep")) writeFileSync(join(work, `${basename(file, ".txt")}.viewer.gp`), ours);
    }
  } finally {
    if (process.argv.includes("--keep")) console.log(`kept outputs in ${work}`);
    else rmSync(work, { recursive: true, force: true });
  }

  console.log(`\n${total - failures} of ${total} cases match htpp${failures ? `; ${failures} differ` : ""}.`);
  return failures === 0 ? 0 : 1;
}

process.exit(main());
