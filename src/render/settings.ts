// Render settings shared by the renderer, the CPU reference and exports.
// Kept free of WebGL so Node scripts can import it.

import type { Statistic } from "../format/derived";
import type { DiffView } from "../format/diff";
import type { Pair } from "../format/htrdrImage";

export type DisplayMode = "image" | "map";

export interface RenderSettings {
  mode: DisplayMode;
  /** Image mode: htpp's exposure and white (Ymax). */
  exposure: number;
  white: number;
  /** Map mode: what to show, and the values on the palette's ends. */
  pair: Pair;
  stat: Statistic;
  range: readonly [number, number];
  log: boolean;
  compare: CompareSettings;
}

export type CompareMode = "none" | "split" | "diff";

export interface CompareSettings {
  mode: CompareMode;
  /** Split: first column showing B, in device pixels of the render target. */
  splitX: number;
  /** Difference: the pair compared and the threshold k in σΔ. */
  pair: Pair;
  k: number;
  /** Difference: show Δ or z = Δ/σΔ, over the symmetric range ±range. */
  view: DiffView;
  range: number;
}

export const NO_COMPARE: CompareSettings = { mode: "none", splitX: 0, pair: 1, k: 2, view: "delta", range: 1 };

/**
 * The range as the shader uses it: log10'd in log mode, and [0, 0] (all
 * black, like htpp's zero-width range) when a log range isn't positive.
 */
export function shaderRange(range: readonly [number, number], log: boolean): [number, number] {
  if (!log) return [range[0], range[1]];
  if (!(range[0] > 0 && range[1] > 0)) return [0, 0];
  return [Math.log10(range[0]), Math.log10(range[1])];
}
