// A/B difference of one (mean, σ) pair. A and B are independent Monte Carlo
// estimates, so σΔ = √(σA² + σB²). A pixel differs significantly when
// |Δ| ≥ k·σΔ. The arithmetic is rounded to float32 at every step so the
// CPU agrees with the shader on which pixels pass the test.

import { meanIndex, sigmaIndex, type Pair } from "./htrdrImage";
import { pixelValue, type LoadedImage } from "./loadedImage";
import { selectInPlace } from "./select";

export type DiffView = "delta" | "z";

export interface DiffSample {
  delta: number;
  sigma: number;
  /** Δ/σΔ; ±Infinity for an exact (σΔ = 0) difference. */
  z: number;
  significant: boolean;
}

const f = Math.fround;

export function diffSample(meanA: number, sigmaA: number, meanB: number, sigmaB: number, k: number): DiffSample {
  const delta = f(meanA - meanB);
  const sigma = f(Math.sqrt(f(f(sigmaA * sigmaA) + f(sigmaB * sigmaB))));
  const significant = delta !== 0 && !(Math.abs(delta) < f(f(k) * sigma));
  return { delta, sigma, z: f(delta / sigma), significant };
}

export function diffAt(a: LoadedImage, b: LoadedImage, x: number, y: number, pair: Pair, k: number): DiffSample {
  const m = meanIndex(pair);
  const s = sigmaIndex(pair);
  return diffSample(pixelValue(a, x, y, m), pixelValue(a, x, y, s), pixelValue(b, x, y, m), pixelValue(b, x, y, s), k);
}

/** Why B can't be compared with A, or null when it can. */
export function incompatibility(a: LoadedImage, b: LoadedImage): string | null {
  if (a.width !== b.width || a.height !== b.height) return `B is ${b.width}×${b.height} but A is ${a.width}×${a.height}`;
  if (a.layout !== b.layout) return `B is a ${b.layout} image but A is ${a.layout}`;
  return null;
}

/**
 * Fraction of pixels expected to pass the k·σ test from noise alone, when A
 * and B estimate the same image: P(|N(0,1)| ≥ k) = erfc(k/√2).
 */
export function noiseOnlyFraction(k: number): number {
  return erfc(k / Math.SQRT2);
}

/** Complementary error function, Numerical Recipes' erfcc (|error| < 1.2e-7). */
function erfc(x: number): number {
  const z = Math.abs(x);
  const t = 1 / (1 + 0.5 * z);
  const r =
    t *
    Math.exp(
      -z * z -
        1.26551223 +
        t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))),
    );
  return x >= 0 ? r : 2 - r;
}

export interface DiffSummary {
  /** Pixels passing the k·σ test, and pixels with finite Δ and σΔ. */
  significant: number;
  finite: number;
  /** Default half-width of the diverging range for each view. */
  autoRange: Record<DiffView, number>;
}

/**
 * Count significant pixels and pick a default range: the 99th percentile of
 * |Δ| (or |z|) over significant pixels, so they spread over the palette.
 */
export function summarizeDiff(a: LoadedImage, b: LoadedImage, pair: Pair, k: number): DiffSummary {
  const count = a.width * a.height;
  const deltas = new Float32Array(count);
  const zs = new Float32Array(count);
  let significant = 0;
  let finiteZ = 0;
  let finite = 0;
  // Read the planes directly; this runs over every pixel of a 4K image.
  const plane = pair < 2 ? 0 : 1;
  const [pa, pb] = [a.planes[plane], b.planes[plane]];
  const m = (pair & 1) * 2;
  for (let i = 0; i < count; i++) {
    const j = i * 4 + m;
    const d = diffSample(pa[j]!, pa[j + 1]!, pb[j]!, pb[j + 1]!, k);
    if (!Number.isFinite(d.delta) || !Number.isFinite(d.sigma)) continue;
    finite++;
    if (!d.significant) continue;
    deltas[significant++] = Math.abs(d.delta);
    if (Number.isFinite(d.z)) zs[finiteZ++] = Math.abs(d.z);
  }
  const p99 = (values: Float32Array, fallback: number) => {
    if (values.length === 0) return fallback;
    const v = selectInPlace(values, Math.min(values.length - 1, Math.round(0.99 * (values.length - 1))));
    return v > 0 ? v : fallback;
  };
  return {
    significant,
    finite,
    autoRange: { delta: p99(deltas.subarray(0, significant), 1), z: p99(zs.subarray(0, finiteZ), Math.max(k, 1) * 2) },
  };
}
