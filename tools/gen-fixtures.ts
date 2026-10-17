// Generate small synthetic htrdr-image files with known structure.
//
//   pnpm fixtures
//
// Output is deterministic (seeded PRNG), so the files can be committed and
// used as test inputs and as htpp parity inputs.

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const OUT_DIR = join(import.meta.dirname, "..", "fixtures");
const W = 64;
const H = 48;
/** Monte Carlo realisations per pixel; sets the noise level. */
const SPP = 64;

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(rand: () => number): number {
  const u = 1 - rand();
  const v = rand();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

type Pixel = [number, number, number, number, number, number, number, number];

/** A noisy estimate of `truth` with relative per-realisation spread `spread`. */
function estimate(rand: () => number, truth: number, spread: number): [number, number] {
  const sigma = (Math.abs(truth) * spread) / Math.sqrt(SPP);
  return [truth + sigma * gaussian(rand), sigma];
}

/** Scene radiance in W/sr/m²: a sky gradient with a bright disc. */
function scene(x: number, y: number): number {
  const u = (x + 0.5) / W;
  const v = (y + 0.5) / H;
  const sky = 2 + 18 * (1 - v);
  const d = Math.hypot(u - 0.7, v - 0.3);
  return sky + (d < 0.12 ? 120 : 0);
}

function timePair(rand: () => number, x: number, y: number): [number, number] {
  return estimate(rand, 150 + 200 * (scene(x, y) / 140), 1.5);
}

function write(name: string, header: string, pixel: (x: number, y: number) => Pixel): void {
  const lines = [`# ${header}`, `${W} ${H}`];
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      lines.push(pixel(x, y).map((v) => v.toPrecision(6)).join(" "));
    }
  }
  writeFileSync(join(OUT_DIR, name), lines.join("\n") + "\n");
  console.log(`wrote fixtures/${name}`);
}

mkdirSync(OUT_DIR, { recursive: true });

{
  const rand = mulberry32(1);
  write("xyz.txt", "Synthetic CIE XYZ image: sky gradient and a bright disc", (x, y) => {
    const L = scene(x, y);
    // Bluish sky (Z > X), yellowish disc.
    const disc = L > 100;
    const X = L * (disc ? 1.05 : 0.9);
    const Z = L * (disc ? 0.8 : 1.3);
    return [...estimate(rand, X, 2), ...estimate(rand, L, 2), ...estimate(rand, Z, 2), ...timePair(rand, x, y)];
  });
}

{
  const rand = mulberry32(2);
  write("longwave.txt", "Synthetic longwave image: brightness temperature and radiance", (x, y) => {
    const v = (y + 0.5) / H;
    const T = 220 + 80 * v; // colder sky at the top, warm ground at the bottom
    const radiance = (5.670374419e-8 * T ** 4) / Math.PI;
    return [...estimate(rand, T, 0.05), ...estimate(rand, radiance, 0.2), 0, 0, ...timePair(rand, x, y)];
  });
}

{
  const rand = mulberry32(3);
  write("shortwave.txt", "Synthetic shortwave image: radiance only", (x, y) => {
    return [0, 0, ...estimate(rand, scene(x, y), 2), 0, 0, ...timePair(rand, x, y)];
  });
}

// An A/B pair of the same XYZ scene with independent noise. B is 20 % brighter
// inside the square x, y in [8, 24). Relative σ is 3.75 % of each image's own
// value, so inside the square z ≈ 0.2 / √(0.0375² + 0.045²) ≈ 3.4 and a 2σ
// test flags most of it, while outside only the ~4.6 % expected from noise pass.
for (const [name, seed, boost] of [["ab-a.txt", 10, false], ["ab-b.txt", 11, true]] as const) {
  const rand = mulberry32(seed);
  write(name, `A/B pair, ${boost ? "B: +20% in the square [8,24)^2" : "A: reference"}`, (x, y) => {
    const inSquare = x >= 8 && x < 24 && y >= 8 && y < 24;
    const L = scene(x, y) * (boost && inSquare ? 1.2 : 1);
    return [...estimate(rand, L, 0.3), ...estimate(rand, L, 0.3), ...estimate(rand, L, 0.3), ...timePair(rand, x, y)];
  });
}
