// Dev page (/parity.html): GPU output vs the CPU reference for every fixture.

import { GRAY, bakeLut, paletteByName, type Palette } from "../color/palettes";
import { STATISTICS, derivedValues } from "../format/derived";
import { summarizeDiff } from "../format/diff";
import { PIXEL_STRIDE, type Pair } from "../format/htrdrImage";
import { loadFromBytes, splitPlanes, type LoadedImage } from "../format/loadedImage";
import { computeStats } from "../format/stats";
import { comparePixels, expectedPixels, type Comparison } from "../render/parity";
import { DEFAULT_RANGE, rangeOf } from "../render/range";
import { NO_COMPARE, Renderer, type RenderSettings } from "../render/renderer";

const TOLERANCE = 1;
const FIXTURES = ["xyz.txt", "ab-a.txt", "ab-b.txt", "longwave.txt", "shortwave.txt"];

/**
 * Stress image for tone mapping: Y sweeps 1e-6..1e6 along x, chromaticity
 * varies along y, plus negative, zero and non-finite pixels.
 */
function stressImage(): LoadedImage {
  const width = 256;
  const height = 64;
  const data = new Float32Array(width * height * PIXEL_STRIDE);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const Y = 10 ** (-6 + (12 * x) / (width - 1));
      const t = y / (height - 1);
      const o = (y * width + x) * PIXEL_STRIDE;
      data[o] = Y * (0.5 + t);
      data[o + 2] = Y;
      data[o + 4] = Y * (1.5 - t);
    }
  }
  data[0] = NaN;
  data[PIXEL_STRIDE * 1 + 2] = Infinity;
  data[PIXEL_STRIDE * 2 + 4] = -5;
  data.fill(0, PIXEL_STRIDE * 3, PIXEL_STRIDE * 4);
  return fromData(width, height, data);
}

function fromData(width: number, height: number, data: Float32Array): LoadedImage {
  const image = { width, height, data };
  return { width, height, planes: splitPlanes(image), layout: "xyz", stats: computeStats(image) };
}

/**
 * B for the stress image: the right half 10 % brighter, the left half
 * identical (Δ = 0), σ only on the bottom half (so the top has σΔ = 0 and
 * exact, infinite-z differences), and a non-finite pixel A doesn't have.
 */
function stressPartner(a: LoadedImage): [LoadedImage, LoadedImage] {
  const { width, height } = a;
  const dataA = new Float32Array(width * height * PIXEL_STRIDE);
  for (let i = 0; i < width * height; i++) {
    for (let k = 0; k < PIXEL_STRIDE; k++) dataA[i * PIXEL_STRIDE + k] = a.planes[k < 4 ? 0 : 1][i * 4 + (k & 3)]!;
  }
  const dataB = dataA.slice();
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const o = (y * width + x) * PIXEL_STRIDE;
      for (const m of [0, 2, 4]) {
        if (x >= width / 2) dataB[o + m]! *= 1.1;
        if (y >= height / 2) {
          dataA[o + m + 1] = Math.abs(dataA[o + m]!) * 0.04;
          dataB[o + m + 1] = Math.abs(dataB[o + m]!) * 0.05;
        }
      }
    }
  }
  dataB[(5 * width + 200) * PIXEL_STRIDE + 2] = NaN;
  return [fromData(width, height, dataA), fromData(width, height, dataB)];
}

interface Case {
  label: string;
  settings: RenderSettings;
  palette: Palette;
  diffPalette?: Palette;
}

/** Palettes with very different structure: 256 entries, 33, 9 and 8 (qualitative). */
const MAP_PALETTES = ["gray", "inferno", "moreland", "jet", "set1"].map(paletteByName);

function cases(img: LoadedImage): Case[] {
  const out: Case[] = [];
  const base = { exposure: 1, white: 1, pair: 0 as Pair, stat: "value" as const, range: [0, 0] as const, log: false, compare: NO_COMPARE };
  const auto = img.stats.autoWhite;
  if (img.layout === "xyz") {
    for (const exposure of [0.25, 1, 4, 32]) {
      for (const [whiteLabel, white] of [["auto", auto], ["auto/4", auto / 4], ["auto×8", auto * 8]] as const) {
        out.push({
          label: `image · exposure ${exposure} · white ${whiteLabel}`,
          settings: { ...base, mode: "image", exposure, white },
          palette: GRAY,
        });
      }
    }
  }
  let paletteIndex = 0;
  for (const pair of [0, 1, 2, 3] as Pair[]) {
    for (const { id: stat } of STATISTICS) {
      const values = derivedValues(img, pair, stat);
      for (const log of [false, true]) {
        const spec = { ...DEFAULT_RANGE, mode: log ? "auto" : "percentile", log } as const;
        const range = rangeOf(values, spec);
        // Rotate palettes so every one meets every kind of data.
        const palette = MAP_PALETTES[paletteIndex++ % MAP_PALETTES.length]!;
        out.push({
          label: `map · pair ${pair} ${stat}${log ? " · log" : " · 1–99 %"} · ${palette.name}`,
          settings: { ...base, mode: "map", pair, stat, range, log },
          palette,
        });
      }
    }
  }
  return out;
}

/** Split and difference cases for an A/B pair. */
function pairCases(a: LoadedImage, b: LoadedImage): Case[] {
  const out: Case[] = [];
  const base = { ...cases(a)[0]!.settings };
  const w = a.width;
  const yValues = derivedValues(a, 1, "value");
  const yRange = rangeOf(yValues, { ...DEFAULT_RANGE, mode: "percentile" });
  for (const splitX of [0, 17, w / 2, w + 5]) {
    const compare = { ...NO_COMPARE, mode: "split" as const, splitX };
    out.push({
      label: `split image · at ${splitX}`,
      settings: { ...base, mode: "image", white: a.stats.autoWhite, compare },
      palette: GRAY,
    });
    out.push({
      label: `split map Y · at ${splitX}`,
      settings: { ...base, mode: "map", pair: 1, range: yRange, compare },
      palette: paletteByName("viridis"),
    });
  }
  for (const pair of [1, 3] as Pair[]) {
    for (const k of [0, 2, 3]) {
      const summary = summarizeDiff(a, b, pair, k);
      for (const view of ["delta", "z"] as const) {
        for (const [rangeLabel, range] of [["auto", summary.autoRange[view]], ["auto/4", summary.autoRange[view] / 4]] as const) {
          const diffPalette = paletteByName(view === "z" ? "rdbu" : "moreland");
          out.push({
            label: `diff · pair ${pair} · k ${k} · ${view} · range ${rangeLabel} · ${diffPalette.name}`,
            settings: { ...base, mode: "map", compare: { mode: "diff", splitX: 0, pair, k, view, range } },
            palette: GRAY,
            diffPalette,
          });
        }
      }
    }
  }
  return out;
}

function row(name: string, label: string, result: Comparison): HTMLTableRowElement {
  const tr = document.createElement("tr");
  const pass = result.mismatches === 0;
  const worst = result.worst ? `#${result.worst.index}: ${result.worst.expected.join(",")} → ${result.worst.actual.join(",")}` : "—";
  for (const [text, cls] of [
    [name, ""],
    [label, ""],
    [String(result.maxDiff), pass ? "pass" : "fail"],
    [String(result.mismatches), pass ? "pass" : "fail"],
    [worst, "muted"],
  ]) {
    const td = document.createElement("td");
    td.textContent = text!;
    if (cls) td.className = cls;
    tr.append(td);
  }
  return tr;
}

async function main(): Promise<void> {
  const renderer = new Renderer(document.getElementById("gl") as HTMLCanvasElement);
  const tbody = document.getElementById("results")!;
  const images = new Map<string, LoadedImage>([["stress", stressImage()]]);
  for (const name of FIXTURES) {
    const bytes = new Uint8Array(await (await fetch(`/fixtures/${name}`)).arrayBuffer());
    images.set(name, loadFromBytes(bytes));
  }
  const pairs: [string, LoadedImage, LoadedImage][] = [
    ["ab-a.txt / ab-b.txt", images.get("ab-a.txt")!, images.get("ab-b.txt")!],
    ["stress / perturbed", ...stressPartner(images.get("stress")!)],
  ];

  let failures = 0;
  let total = 0;
  let maxDiff = 0;
  const run = (name: string, a: LoadedImage, b: LoadedImage | undefined, list: Case[]) => {
    renderer.setImage(0, a);
    if (b) renderer.setImage(1, b);
    else renderer.clearImage(1);
    for (const { label, settings, palette, diffPalette } of list) {
      renderer.setPalette(bakeLut(palette));
      if (diffPalette) renderer.setDiffPalette(bakeLut(diffPalette));
      const expected = expectedPixels(a, settings, { palette, b, diffPalette });
      const result = comparePixels(expected, renderer.renderToPixels(settings), TOLERANCE);
      total++;
      if (result.mismatches > 0) failures++;
      maxDiff = Math.max(maxDiff, result.maxDiff);
      tbody.append(row(name, label, result));
    }
  };
  for (const [name, img] of images) run(name, img, undefined, cases(img));
  for (const [name, a, b] of pairs) run(name, a, b, pairCases(a, b));

  const summary = document.getElementById("summary")!;
  summary.textContent = failures === 0 ? `All ${total} cases pass (max Δ ${maxDiff}).` : `${failures} of ${total} cases fail (max Δ ${maxDiff}).`;
  summary.className = failures === 0 ? "pass" : "fail";
  Object.assign(window, { parityResult: { total, failures, maxDiff } });
}

main().catch((err) => {
  const summary = document.getElementById("summary")!;
  summary.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
  summary.className = "fail";
  Object.assign(window, { parityResult: { error: String(err) } });
});
