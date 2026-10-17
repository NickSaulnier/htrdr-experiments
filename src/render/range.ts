// Map-mode range: which values land on the palette's ends.

import { selectInPlace } from "../format/select";

export type RangeMode = "auto" | "percentile" | "manual";

export interface RangeSpec {
  mode: RangeMode;
  /** Percentile mode: lower and upper percentiles, in [0, 100]. */
  low: number;
  high: number;
  /** Manual mode. */
  manual: readonly [number, number];
  /** Map log10 of the value. Non-positive values land on the low end. */
  log: boolean;
}

export const DEFAULT_RANGE: RangeSpec = { mode: "auto", low: 1, high: 99, manual: [0, 1], log: false };

/**
 * The [min, max] mapped to the palette ends, from one value per pixel in any
 * order. Auto is htpp's choice: the min and max of the image. Percentiles
 * use the nearest rank among the values that count. Non-finite values never
 * count, and in log mode neither do non-positive ones. A range of zero width
 * maps everything to black, as in htpp.
 *
 * Linear in the number of pixels (a scan, or quickselect for percentiles),
 * so it stays interactive on 4K images.
 */
export function rangeOf(values: Float32Array, spec: RangeSpec): [number, number] {
  if (spec.mode === "manual") return [spec.manual[0], spec.manual[1]];
  const counts = spec.log ? (v: number) => v > 0 && v < Infinity : Number.isFinite;

  if (spec.mode === "auto") {
    let min = Infinity;
    let max = -Infinity;
    for (let i = 0; i < values.length; i++) {
      const v = values[i]!;
      if (!counts(v)) continue;
      if (v < min) min = v;
      if (v > max) max = v;
    }
    return min <= max ? [min, max] : [0, 0];
  }

  const kept = new Float32Array(values.length);
  let n = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i]!;
    if (counts(v)) kept[n++] = v;
  }
  if (n === 0) return [0, 0];
  const pool = kept.subarray(0, n);
  const rank = (pct: number) => Math.min(n - 1, Math.max(0, Math.round((pct / 100) * (n - 1))));
  const lo = Math.min(spec.low, spec.high);
  const hi = Math.max(spec.low, spec.high);
  return [selectInPlace(pool, rank(lo)), selectInPlace(pool, rank(hi))];
}
