import { describe, expect, it } from "vitest";
import { GRAY } from "../src/color/palettes";
import { filmic, filmicStable, toneMapPixelBytes } from "../src/color/tonemap";
import { loadFromBytes } from "../src/format/loadedImage";
import { comparePixels, expectedPixels } from "../src/render/parity";
import { NO_COMPARE, type RenderSettings } from "../src/render/renderer";

const image = (text: string) => loadFromBytes(new TextEncoder().encode(text));
const imageMode = (exposure: number, white: number): RenderSettings => ({ mode: "image", exposure, white, pair: 0, stat: "value", range: [0, 0], log: false, compare: NO_COMPARE });

describe("filmicStable", () => {
  it("equals htpp's filmic curve in double precision", () => {
    for (let e = -8; e <= 8; e += 0.25) {
      const x = 10 ** e;
      expect(filmicStable(x)).toBeCloseTo(filmic(x), 12);
      expect(Math.abs(filmicStable(x) - filmic(x)) / filmic(x)).toBeLessThan(1e-7);
    }
    expect(filmicStable(0)).toBe(0);
  });

  it("keeps its precision near 0 in float32, unlike the direct form", () => {
    const f = Math.fround;
    const A = f(0.15), B = f(0.5), C = f(0.1), D = f(0.2), E = f(0.02), F = f(0.3);
    const x = f(1e-6);
    // Both forms evaluated with float32 rounding after every operation.
    const direct = f(f(f(f(x * f(f(A * x) + f(C * B))) + f(D * E)) / f(f(x * f(f(A * x) + B)) + f(D * F))) - f(E / F));
    const stable = f(f(x * f(f(f(F - E) * f(A * x)) + f(f(f(F * C) - E) * B))) / f(F * f(f(x * f(f(A * x) + B)) + f(D * F))));
    const exact = filmic(1e-6);
    expect(Math.abs(stable - exact) / exact).toBeLessThan(1e-6);
    expect(Math.abs(direct - exact) / exact).toBeGreaterThan(1e-3);
  });
});

describe("expectedPixels", () => {
  it("tone maps XYZ pixels row by row in image mode", () => {
    const img = image("2 1\n1 0 2 0 3 0 0 0\n4 0 5 0 6 0 0 0\n");
    const px = expectedPixels(img, imageMode(1, 5), { palette: GRAY });
    expect(Array.from(px.subarray(0, 3))).toEqual(toneMapPixelBytes(1, 2, 3, { exposure: 1, white: 5 }));
    expect(Array.from(px.subarray(4, 7))).toEqual(toneMapPixelBytes(4, 5, 6, { exposure: 1, white: 5 }));
    expect(px[3]).toBe(255);
  });

  it("flags non-finite pixels in magenta and blacks out a non-positive white", () => {
    const img = image("2 1\nnan 0 1 0 1 0 0 0\n1 0 1 0 1 0 0 0\n");
    expect(Array.from(expectedPixels(img, imageMode(1, 1), { palette: GRAY }))).toEqual([255, 0, 255, 255, 255, 255, 255, 255]);
    expect(Array.from(expectedPixels(img, imageMode(1, 0), { palette: GRAY }).subarray(4))).toEqual([0, 0, 0, 255]);
  });

  it("maps the chosen value through the palette in map mode", () => {
    const img = image("3 1\n0 0 0 0 0 0 0 0\n0 5 0 0 0 0 0 0\n0 10 0 0 0 0 0 0\n");
    const px = expectedPixels(img, { mode: "map", exposure: 1, white: 1, pair: 0, stat: "sigma", range: [0, 10], log: false, compare: NO_COMPARE }, { palette: GRAY });
    expect([px[0], px[4], px[8]]).toEqual([0, 128, 255]);
  });
});

describe("comparePixels", () => {
  it("reports the max difference, mismatches over tolerance and the worst pixel", () => {
    const a = new Uint8Array([10, 10, 10, 255, 20, 20, 20, 255, 30, 30, 30, 255]);
    const b = new Uint8Array([11, 10, 10, 255, 20, 25, 20, 255, 30, 30, 32, 255]);
    expect(comparePixels(a, b, 1)).toEqual({
      maxDiff: 5,
      mismatches: 2,
      worst: { index: 1, expected: [20, 20, 20], actual: [20, 25, 20] },
    });
  });

  it("ignores alpha", () => {
    expect(comparePixels(new Uint8Array([1, 2, 3, 0]), new Uint8Array([1, 2, 3, 255])).maxDiff).toBe(0);
  });
});
