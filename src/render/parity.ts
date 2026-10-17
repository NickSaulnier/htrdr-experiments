// CPU-side expected output for a render, and a byte-wise comparison. The
// parity page renders the same settings on the GPU and diffs the two.

import { fetchColor, mapValue, type Palette, type RGB } from "../color/palettes";
import { filmic, toByte, toneMapPixelBytes } from "../color/tonemap";
import { derivedValue } from "../format/derived";
import { diffAt } from "../format/diff";
import { pixelValue, type LoadedImage } from "../format/loadedImage";
import { shaderRange, type RenderSettings } from "./settings";

const NON_FINITE: readonly number[] = [255, 0, 255];
/** Gray for an insignificant difference: INSIGNIFICANT in view.frag, as bytes. */
const INSIGNIFICANT: readonly number[] = [77, 77, 77];

export interface ExpectedOptions {
  palette: Palette;
  /** The second image, for split and difference modes. */
  b?: LoadedImage;
  /** The diverging palette used in difference mode. */
  diffPalette?: Palette;
}

/** RGBA8 pixels, rows top-down, as the shader should produce them at 1:1. */
export function expectedPixels(img: LoadedImage, s: RenderSettings, options: ExpectedOptions): Uint8Array {
  const { palette, b } = options;
  const compare = b ? s.compare.mode : "none";
  const out = new Uint8Array(img.width * img.height * 4);
  for (let y = 0; y < img.height; y++) {
    for (let x = 0; x < img.width; x++) {
      let rgb: readonly number[];
      if (compare === "diff") rgb = expectedDifference(img, b!, x, y, s, options.diffPalette ?? palette);
      // The shader tests the pixel centre, gl_FragCoord.x = x + 0.5.
      else if (compare === "split" && x + 0.5 >= s.compare.splitX) rgb = expectedSingle(b!, x, y, s, palette);
      else rgb = expectedSingle(img, x, y, s, palette);
      out.set([rgb[0]!, rgb[1]!, rgb[2]!, 255], (y * img.width + x) * 4);
    }
  }
  return out;
}

function expectedSingle(img: LoadedImage, x: number, y: number, s: RenderSettings, palette: Palette): readonly number[] {
  if (s.mode === "image") {
    const [X, Y, Z] = [0, 2, 4].map((k) => pixelValue(img, x, y, k)) as [number, number, number];
    if (![X, Y, Z].every(Number.isFinite)) return NON_FINITE;
    if (!(filmic(s.white * s.exposure) > 0)) return [0, 0, 0];
    return toneMapPixelBytes(X, Y, Z, s);
  }
  const v = derivedValue(img, x, y, s.pair, s.stat);
  return Number.isFinite(v) ? mapColor(v, s, palette).map(toByte) : NON_FINITE;
}

function expectedDifference(a: LoadedImage, b: LoadedImage, x: number, y: number, s: RenderSettings, palette: Palette): readonly number[] {
  const { pair, k, view, range } = s.compare;
  const d = diffAt(a, b, x, y, pair, k);
  if (!Number.isFinite(d.delta) || !Number.isFinite(d.sigma)) return NON_FINITE;
  if (!d.significant) return INSIGNIFICANT;
  if (!(range > 0)) return [0, 0, 0];
  return mapValue(view === "z" ? d.z : d.delta, [-range, range], palette).map(toByte);
}

/** htpp's pp_map, extended with the viewer's log scale. */
function mapColor(v: number, s: RenderSettings, palette: Palette): RGB {
  const range = shaderRange(s.range, s.log);
  if (!s.log) return mapValue(v, range, palette);
  if (range[1] === range[0]) return [0, 0, 0];
  return v > 0 ? mapValue(Math.log10(v), range, palette) : fetchColor(palette, 0);
}

export interface Comparison {
  /** Largest absolute difference over all RGB channels. */
  maxDiff: number;
  /** Pixels with any channel differing by more than `tolerance`. */
  mismatches: number;
  /** First pixel with the largest difference. */
  worst: { index: number; expected: number[]; actual: number[] } | null;
}

export function comparePixels(expected: Uint8Array, actual: Uint8Array, tolerance = 1): Comparison {
  if (expected.length !== actual.length) throw new Error("pixel buffers differ in size");
  let maxDiff = 0;
  let mismatches = 0;
  let worst: Comparison["worst"] = null;
  for (let i = 0; i < expected.length; i += 4) {
    let d = 0;
    for (let c = 0; c < 3; c++) d = Math.max(d, Math.abs(expected[i + c]! - actual[i + c]!));
    if (d > tolerance) mismatches++;
    if (d > maxDiff) {
      maxDiff = d;
      worst = { index: i / 4, expected: Array.from(expected.subarray(i, i + 3)), actual: Array.from(actual.subarray(i, i + 3)) };
    }
  }
  return { maxDiff, mismatches, worst };
}
