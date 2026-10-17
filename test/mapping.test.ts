import { describe, expect, it } from "vitest";
import { DEFAULT_PALETTE, LUT_SIZE, PALETTES, bakeLut, fetchColor, paletteByName } from "../src/color/palettes";
import { derivedValue, derivedValues } from "../src/format/derived";
import { loadFromBytes } from "../src/format/loadedImage";
import { DEFAULT_RANGE, rangeOf } from "../src/render/range";
import { selectInPlace } from "../src/format/select";
import { shaderRange } from "../src/render/renderer";
import { formatEstimate, formatNumber, formatPercent } from "../src/ui/format";

const image = (text: string) => loadFromBytes(new TextEncoder().encode(text));
const bytes = (c: readonly number[]) => c.map((v) => Math.round(v * 255));

describe("palettes", () => {
  it("covers star-cmap's list except the unlicensed tables, plus gray", () => {
    const names = PALETTES.map((p) => p.name);
    expect(names).toHaveLength(42);
    for (const n of ["inferno", "viridis", "magma", "plasma", "moreland", "jet", "rdbu", "set1", "ylorrd", "gray"]) {
      expect(names).toContain(n);
    }
    for (const n of ["parula", "chromajs", "gnpu", "sand", "whgnbu", "whylrd", "ylrd"]) expect(names).not.toContain(n);
    expect(DEFAULT_PALETTE).toBe("inferno");
  });

  it("loads the upstream tables", () => {
    expect(paletteByName("viridis").colors).toHaveLength(256);
    expect(bytes(paletteByName("viridis").colors[0]!)).toEqual([0x44, 0x01, 0x54]);
    expect(bytes(paletteByName("inferno").colors[255]!)).toEqual([0xfc, 0xff, 0xa4]);
    expect(paletteByName("rdbu").colors).toHaveLength(8);
    expect(bytes(paletteByName("rdbu").colors[0]!)).toEqual([0xb2, 0x18, 0x2b]);
  });

  it("reproduces Moreland's published cool-warm table", () => {
    // 257 samples like Moreland's own table (and star-cmap's): with only 33,
    // linear interpolation between samples drifts ~4/255 near the warm end.
    const m = paletteByName("moreland");
    expect(m.colors).toHaveLength(257);
    expect(bytes(fetchColor(m, 0))).toEqual([59, 76, 192]);
    expect(bytes(fetchColor(m, 0.25))).toEqual([141, 176, 254]);
    expect(bytes(fetchColor(m, 0.5))).toEqual([221, 221, 221]);
    expect(bytes(fetchColor(m, 1))).toEqual([180, 4, 38]);
  });

  it("builds jet from its piecewise-linear definition", () => {
    const jet = paletteByName("jet");
    expect(bytes(fetchColor(jet, 0))).toEqual([0, 0, 128]);
    expect(bytes(fetchColor(jet, 0.5))).toEqual([128, 255, 128]);
    expect(bytes(fetchColor(jet, 1))).toEqual([128, 0, 0]);
  });

  it("bakes a LUT linear filtering can't push past one step from the exact palette", () => {
    for (const p of PALETTES) {
      const lut = bakeLut(p);
      expect(lut.length).toBe(LUT_SIZE * 4);
      // Worst case for GPU filtering: halfway between two LUT entries.
      for (let i = 0; i < LUT_SIZE - 1; i++) {
        const exact = fetchColor(p, (i + 0.5) / (LUT_SIZE - 1));
        for (let c = 0; c < 3; c++) {
          const filtered = (lut[i * 4 + c]! + lut[(i + 1) * 4 + c]!) / 2;
          expect(Math.abs(filtered - exact[c]! * 255)).toBeLessThanOrEqual(1);
        }
      }
    }
  });
});

describe("derived statistics", () => {
  const img = image("2 1\n4 1 -10 5 0 0 3 0.5\n2 0 0 2 0 0 1 0\n");

  it("returns the mean, σ or σ/|mean| of any pair", () => {
    expect(derivedValue(img, 0, 0, 0, "value")).toBe(4);
    expect(derivedValue(img, 0, 0, 0, "sigma")).toBe(1);
    expect(derivedValue(img, 0, 0, 0, "relative")).toBe(0.25);
    expect(derivedValue(img, 0, 0, 1, "relative")).toBe(0.5);
    expect(derivedValue(img, 0, 0, 3, "value")).toBe(3);
    expect(derivedValue(img, 1, 0, 1, "relative")).toBe(Infinity);
  });

  it("computes all pixels at once, matching the per-pixel form", () => {
    for (const pair of [0, 1, 2, 3] as const) {
      for (const stat of ["value", "sigma", "relative"] as const) {
        const all = derivedValues(img, pair, stat);
        expect(Array.from(all)).toEqual([0, 1].map((x) => derivedValue(img, x, 0, pair, stat)));
      }
    }
  });
});

describe("ranges", () => {
  const values = new Float32Array([5, NaN, -2, 0, 1, Infinity, 3, 4, 2]);

  it("auto is the min and max of the finite values, like htpp", () => {
    expect(rangeOf(values, DEFAULT_RANGE)).toEqual([-2, 5]);
  });

  it("auto log ignores non-positive values", () => {
    expect(rangeOf(values, { ...DEFAULT_RANGE, log: true })).toEqual([1, 5]);
    expect(rangeOf(new Float32Array([-1, 0]), { ...DEFAULT_RANGE, log: true })).toEqual([0, 0]);
  });

  it("percentile picks the nearest rank, in either order, from unsorted input", () => {
    const shuffled = Float32Array.from({ length: 101 }, (_, i) => (i * 37) % 101);
    expect(rangeOf(shuffled, { ...DEFAULT_RANGE, mode: "percentile", low: 1, high: 99 })).toEqual([1, 99]);
    expect(rangeOf(shuffled, { ...DEFAULT_RANGE, mode: "percentile", low: 90, high: 10 })).toEqual([10, 90]);
    expect(rangeOf(values, { ...DEFAULT_RANGE, mode: "percentile", low: 0, high: 100 })).toEqual([-2, 5]);
    expect(rangeOf(values, { ...DEFAULT_RANGE, mode: "percentile", low: 0, high: 100, log: true })).toEqual([1, 5]);
  });

  it("does not reorder the caller's values", () => {
    const copy = values.slice();
    rangeOf(values, { ...DEFAULT_RANGE, mode: "percentile" });
    expect(Array.from(values)).toEqual(Array.from(copy));
  });

  it("manual returns the given range as is", () => {
    expect(rangeOf(values, { ...DEFAULT_RANGE, mode: "manual", manual: [7, -7] })).toEqual([7, -7]);
  });

  it("empty input maps to a zero-width range", () => {
    expect(rangeOf(new Float32Array(), DEFAULT_RANGE)).toEqual([0, 0]);
    expect(rangeOf(new Float32Array([NaN]), { ...DEFAULT_RANGE, mode: "percentile" })).toEqual([0, 0]);
  });

  it("log ranges reach the shader as log10, or black when not positive", () => {
    expect(shaderRange([10, 1000], true)).toEqual([1, 3]);
    expect(shaderRange([0, 1000], true)).toEqual([0, 0]);
    expect(shaderRange([-1, 5], false)).toEqual([-1, 5]);
  });
});

describe("formatting", () => {
  it("rounds an estimate to two significant digits of σ", () => {
    expect(formatEstimate(144.597, 33.6406)).toBe("145 ± 34");
    expect(formatEstimate(2.55e-1, 2.9e-3)).toBe("0.2550 ± 0.0029");
    expect(formatEstimate(306.484, 0.2597)).toBe("306.48 ± 0.26");
    expect(formatEstimate(1, 3)).toBe("1.0 ± 3.0");
  });

  it("shares one exponent for very large or small values", () => {
    expect(formatEstimate(2.55e-4, 2.9e-5)).toBe("(2.55 ± 0.29)e-4");
    expect(formatEstimate(1.234e8, 5.6e6)).toBe("(1.234 ± 0.056)e8");
  });

  it("falls back to the bare value without a usable σ", () => {
    expect(formatEstimate(3.14159, 0)).toBe("3.142");
    expect(formatEstimate(NaN, 1)).toBe("NaN");
  });

  it("formats percentages and plain numbers", () => {
    expect(formatPercent(0.2326)).toBe("23 %");
    expect(formatPercent(0.0004567)).toBe("0.046 %");
    expect(formatPercent(Infinity)).toBe("—");
    expect(formatNumber(0)).toBe("0");
    expect(formatNumber(12345.678)).toBe("12350");
    expect(formatNumber(1.5e-7)).toBe("1.5e-7");
    expect(formatNumber(2e9)).toBe("2e+9");
  });
});

describe("selectInPlace", () => {
  it("agrees with a full sort on random, duplicate-heavy and ordered input", () => {
    let seed = 1;
    const rand = () => ((seed = (seed * 1103515245 + 12345) >>> 0) / 4294967296);
    const inputs = [
      Float32Array.from({ length: 1001 }, rand),
      Float32Array.from({ length: 500 }, () => Math.floor(rand() * 4)),
      Float32Array.from({ length: 300 }, (_, i) => i),
      Float32Array.from({ length: 300 }, (_, i) => -i),
      new Float32Array(64).fill(3),
      Float32Array.of(Infinity, -Infinity, 0, 1),
    ];
    for (const input of inputs) {
      const sorted = input.slice().sort();
      for (const k of [0, 1, input.length >> 1, input.length - 2, input.length - 1]) {
        expect(selectInPlace(input.slice(), k)).toBe(sorted[k]);
      }
      // Several ranks from one array, as rangeOf does.
      const shared = input.slice();
      expect([selectInPlace(shared, 0), selectInPlace(shared, input.length - 1)]).toEqual([sorted[0], sorted[input.length - 1]]);
    }
  });
});
