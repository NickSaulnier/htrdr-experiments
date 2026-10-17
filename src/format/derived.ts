// What map mode shows for a (mean, σ) pair: the mean, its standard error, or
// the relative error σ/|mean|. The shader computes the same thing.

import { meanIndex, sigmaIndex, type Pair } from "./htrdrImage";
import { pixelValue, type LoadedImage } from "./loadedImage";

export type Statistic = "value" | "sigma" | "relative";

export const STATISTICS: readonly { id: Statistic; label: string }[] = [
  { id: "value", label: "Value" },
  { id: "sigma", label: "Std. error σ" },
  { id: "relative", label: "Relative error σ/|μ|" },
];

/** Shader encoding of a Statistic. */
export const STATISTIC_CODE: Record<Statistic, number> = { value: 0, sigma: 1, relative: 2 };

/** Relative error in float32 arithmetic, as the shader computes it. */
const relative = (mean: number, sigma: number) => Math.fround(sigma / Math.abs(mean));

export function derivedValue(img: LoadedImage, x: number, y: number, pair: Pair, stat: Statistic): number {
  if (stat === "value") return pixelValue(img, x, y, meanIndex(pair));
  if (stat === "sigma") return pixelValue(img, x, y, sigmaIndex(pair));
  return relative(pixelValue(img, x, y, meanIndex(pair)), pixelValue(img, x, y, sigmaIndex(pair)));
}

/** The derived value of every pixel, row-major. */
export function derivedValues(img: LoadedImage, pair: Pair, stat: Statistic): Float32Array {
  const count = img.width * img.height;
  const out = new Float32Array(count);
  const plane = img.planes[pair < 2 ? 0 : 1];
  const m = (pair & 1) * 2;
  for (let i = 0; i < count; i++) {
    const mean = plane[i * 4 + m]!;
    const sigma = plane[i * 4 + m + 1]!;
    out[i] = stat === "value" ? mean : stat === "sigma" ? sigma : relative(mean, sigma);
  }
  return out;
}
