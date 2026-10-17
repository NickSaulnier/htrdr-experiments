// The parsed image as the app holds it: the 8 values per pixel split into two
// RGBA planes, ready for upload as RGBA32F textures and for CPU lookups.

import { PIXEL_STRIDE, detectLayout, type HtrdrImage, type Layout } from "./htrdrImage";
import { parseHtrdrImage, type ParseOptions } from "./parse";
import { computeStats, type ImageStats } from "./stats";

export interface LoadedImage {
  width: number;
  height: number;
  /** planes[0] holds values 0–3 of each pixel, planes[1] values 4–7. */
  planes: [Float32Array, Float32Array];
  layout: Layout;
  stats: ImageStats;
}

export function splitPlanes(img: HtrdrImage): [Float32Array, Float32Array] {
  const count = img.width * img.height;
  const lo = new Float32Array(count * 4);
  const hi = new Float32Array(count * 4);
  const { data } = img;
  for (let i = 0; i < count; i++) {
    const s = i * PIXEL_STRIDE;
    const d = i * 4;
    lo[d] = data[s]!;
    lo[d + 1] = data[s + 1]!;
    lo[d + 2] = data[s + 2]!;
    lo[d + 3] = data[s + 3]!;
    hi[d] = data[s + 4]!;
    hi[d + 1] = data[s + 5]!;
    hi[d + 2] = data[s + 6]!;
    hi[d + 3] = data[s + 7]!;
  }
  return [lo, hi];
}

/** Value index 0–7 of the pixel at (x, y), counted from the top-left. */
export function pixelValue(img: LoadedImage, x: number, y: number, valueIndex: number): number {
  const plane = img.planes[valueIndex < 4 ? 0 : 1];
  return plane[(y * img.width + x) * 4 + (valueIndex & 3)]!;
}

/** Everything the parse worker does, as a plain function for testing. */
export function loadFromBytes(bytes: Uint8Array, options: ParseOptions = {}): LoadedImage {
  const image = parseHtrdrImage(bytes, options);
  return {
    width: image.width,
    height: image.height,
    planes: splitPlanes(image),
    layout: detectLayout(image),
    stats: computeStats(image),
  };
}
