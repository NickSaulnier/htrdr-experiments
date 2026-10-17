import { describe, expect, it } from "vitest";
import { GRAY, bakeLut, fetchColor, mapValue, type Palette } from "../src/color/palettes";
import { D65, XYZ_TO_SRGB, filmic, srgbEncode, toByte, toneMapPixel, toneMapPixelBytes } from "../src/color/tonemap";
import { PIXEL_STRIDE } from "../src/format/htrdrImage";
import { autoWhite, computeStats, quantile } from "../src/format/stats";

describe("htpp image mode reference", () => {
  it("filmic curve maps 0 to 0 and increases monotonically", () => {
    expect(filmic(0)).toBeCloseTo(0, 15);
    let prev = 0;
    for (const x of [0.01, 0.1, 1, 10, 100]) {
      expect(filmic(x)).toBeGreaterThan(prev);
      prev = filmic(x);
    }
    // Hand-computed: (1*(0.15+0.05)+0.004)/(1*(0.15+0.5)+0.06) - 0.02/0.3
    expect(filmic(1)).toBeCloseTo(0.204 / 0.71 - 0.02 / 0.3, 15);
  });

  it("maps X = Y = Z = white to sRGB white regardless of exposure", () => {
    // Tone mapping sends each channel to 1, and htpp's D65 multiply then turns
    // (1, 1, 1) into the D65 white point, which is sRGB (1, 1, 1).
    const white = 25;
    for (const exposure of [0.1, 1, 7]) {
      const [r, g, b] = toneMapPixel(white, white, white, { exposure, white });
      for (const c of [r, g, b]) expect(c).toBeCloseTo(1, 3);
    }
  });

  it("matches a hand-evaluated pixel", () => {
    const params = { exposure: 2, white: 30 };
    const [X, Y, Z] = [3, 4, 5];
    const ws = filmic(params.white * params.exposure);
    const xyz = [X, Y, Z].map((c, i) => (filmic(c * params.exposure) / ws) * D65[i]!);
    const expected = XYZ_TO_SRGB.map((row) => srgbEncode(Math.max(row[0] * xyz[0]! + row[1] * xyz[1]! + row[2] * xyz[2]!, 0)));
    expect(toneMapPixel(X, Y, Z, params)).toEqual(expected);
  });

  it("clamps negative linear RGB to black before encoding", () => {
    // Pure Z drives linear R negative.
    const [r] = toneMapPixel(0, 0, 10, { exposure: 1, white: 10 });
    expect(r).toBe(0);
  });

  it("quantizes like img_write_ppm", () => {
    expect(toByte(-1)).toBe(0);
    expect(toByte(2)).toBe(255);
    expect(toByte(0.5)).toBe(128);
    expect(toByte(127.4 / 255)).toBe(127);
    expect(toneMapPixelBytes(0, 0, 0, { exposure: 1, white: 1 })).toEqual([0, 0, 0]);
  });

  it("uses the sRGB OETF with its linear toe", () => {
    expect(srgbEncode(0.001)).toBeCloseTo(0.01292, 12);
    expect(srgbEncode(1)).toBeCloseTo(1, 12);
  });
});

describe("stats", () => {
  const img = (ys: number[]) => {
    const data = new Float32Array(ys.length * PIXEL_STRIDE);
    ys.forEach((y, i) => (data[i * PIXEL_STRIDE + 2] = y));
    return { width: ys.length, height: 1, data };
  };

  it("auto white takes the sorted Y at index floor(0.995 N), like htpp", () => {
    const ys = Array.from({ length: 1000 }, (_, i) => 999 - i);
    expect(autoWhite(img(ys))).toBe(995);
    expect(autoWhite(img([5, 1, 3]))).toBe(5); // floor(2.985) = 2
    // NaNs sort last, as in a typed-array sort.
    expect(autoWhite(img([NaN, 2, 1]))).toBeNaN();
    expect(autoWhite(img([...Array.from({ length: 999 }, (_, i) => i), NaN]))).toBe(995);
  });

  it("computes min/max over finite values and counts the rest", () => {
    const stats = computeStats(img([3, NaN, -1, Infinity, 7]));
    expect(stats.values[2]).toEqual({ min: -1, max: 7, nonFinite: 2 });
    expect(stats.values[0]).toEqual({ min: 0, max: 0, nonFinite: 0 });
  });

  it("quantile ignores non-finite values", () => {
    expect(quantile(img([NaN, 1, 2, 3, 4]), 2, 0.5)).toBe(3);
    expect(quantile(img([NaN]), 2, 0.5)).toBeNaN();
  });
});

describe("htpp map mode reference", () => {
  const ramp: Palette = { name: "ramp", colors: [[0, 0, 0], [1, 0, 0], [1, 1, 1]] };

  it("interpolates linearly between control colors", () => {
    expect(fetchColor(ramp, 0)).toEqual([0, 0, 0]);
    expect(fetchColor(ramp, 0.25)).toEqual([0.5, 0, 0]);
    expect(fetchColor(ramp, 0.75)).toEqual([1, 0.5, 0.5]);
    expect(fetchColor(ramp, 1)).toEqual([1, 1, 1]);
  });

  it("clamps to the range and maps a zero-width range to black", () => {
    expect(mapValue(-5, [0, 10], GRAY)).toEqual([0, 0, 0]);
    expect(mapValue(15, [0, 10], GRAY)).toEqual([1, 1, 1]);
    expect(mapValue(5, [0, 10], GRAY)).toEqual([0.5, 0.5, 0.5]);
    expect(mapValue(4, [4, 4], GRAY)).toEqual([0, 0, 0]);
  });

  it("bakes an RGBA8 lookup table", () => {
    const lut = bakeLut(GRAY, 3);
    expect(Array.from(lut)).toEqual([0, 0, 0, 255, 128, 128, 128, 255, 255, 255, 255, 255]);
  });
});
