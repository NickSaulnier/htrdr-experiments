import { PIXEL_STRIDE, meanIndex, type HtrdrImage } from "./htrdrImage";
import { selectInPlace } from "./select";

export interface ValueStats {
  /** Over finite values only; NaN when the image has none. */
  min: number;
  max: number;
  nonFinite: number;
}

export interface ImageStats {
  /** One entry per value index 0..7 (mean and σ of each pair). */
  values: ValueStats[];
  /** htpp's automatic white: the Y mean at 99.5 % of the sorted pixels. */
  autoWhite: number;
}

export function computeStats(img: HtrdrImage): ImageStats {
  const { data } = img;
  const values: ValueStats[] = [];
  for (let k = 0; k < PIXEL_STRIDE; k++) {
    let min = Infinity;
    let max = -Infinity;
    let nonFinite = 0;
    for (let i = k; i < data.length; i += PIXEL_STRIDE) {
      const v = data[i]!;
      if (!Number.isFinite(v)) {
        nonFinite++;
        continue;
      }
      if (v < min) min = v;
      if (v > max) max = v;
    }
    values.push(min <= max ? { min, max, nonFinite } : { min: NaN, max: NaN, nonFinite });
  }
  return { values, autoWhite: autoWhite(img) };
}

/**
 * Port of htpp's compute_XYZ_normalization_factor: the Y at index
 * floor(0.995 N) of the sorted Y values. Found by quickselect rather than a
 * full sort; NaNs count as sorting last.
 */
export function autoWhite(img: HtrdrImage): number {
  const count = img.width * img.height;
  const y = new Float32Array(count);
  const offset = meanIndex(1);
  let n = 0;
  for (let i = 0; i < count; i++) {
    const v = img.data[i * PIXEL_STRIDE + offset]!;
    if (!Number.isNaN(v)) y[n++] = v;
  }
  const k = Math.floor(count * 0.995);
  return k < n ? selectInPlace(y.subarray(0, n), k) : NaN;
}

/** Value at quantile q in [0, 1] of one value index, ignoring non-finite values. */
export function quantile(img: HtrdrImage, valueIndex: number, q: number): number {
  const values = new Float32Array(img.width * img.height);
  let n = 0;
  for (let i = valueIndex; i < img.data.length; i += PIXEL_STRIDE) {
    const v = img.data[i]!;
    if (Number.isFinite(v)) values[n++] = v;
  }
  if (n === 0) return NaN;
  return selectInPlace(values.subarray(0, n), Math.min(n - 1, Math.floor(q * n)));
}
