import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { diffAt, diffSample, incompatibility, noiseOnlyFraction, summarizeDiff } from "../src/format/diff";
import { loadFromBytes } from "../src/format/loadedImage";

const fixture = (name: string) => loadFromBytes(readFileSync(join(import.meta.dirname, "..", "fixtures", name)));
const image = (text: string) => loadFromBytes(new TextEncoder().encode(text));

describe("diffSample", () => {
  it("adds independent errors in quadrature", () => {
    const d = diffSample(10, 3, 4, 4, 2);
    expect(d.delta).toBe(6);
    expect(d.sigma).toBe(5);
    expect(d.z).toBeCloseTo(1.2, 6);
    expect(d.significant).toBe(false); // 6 < 2·5
  });

  it("is significant at |Δ| ≥ kσΔ, either sign", () => {
    expect(diffSample(10, 3, 0, 4, 2).significant).toBe(true); // 10 = 2·5
    expect(diffSample(0, 3, 10, 4, 2).significant).toBe(true);
    expect(diffSample(9.9, 3, 0, 4, 2).significant).toBe(false);
  });

  it("treats exact differences as significant and equal values as not", () => {
    const exact = diffSample(2, 0, 1, 0, 2);
    expect(exact.significant).toBe(true);
    expect(exact.z).toBe(Infinity);
    expect(diffSample(1, 0, 1, 0, 2).significant).toBe(false);
    expect(diffSample(1, 0.5, 1, 0.5, 0).significant).toBe(false); // k = 0: only Δ = 0 is gray
    expect(diffSample(1.001, 0.5, 1, 0.5, 0).significant).toBe(true);
  });
});

describe("incompatibility", () => {
  const a = image("2 1\n1 0 1 0 1 0 0 0\n1 0 1 0 1 0 0 0\n");
  it("accepts images of the same size and layout", () => {
    expect(incompatibility(a, a)).toBeNull();
  });
  it("rejects a different size or layout", () => {
    expect(incompatibility(a, image("1 1\n1 0 1 0 1 0 0 0\n"))).toBe("B is 1×1 but A is 2×1");
    expect(incompatibility(a, image("2 1\n300 1 2 0 0 0 0 0\n300 1 2 0 0 0 0 0\n"))).toBe("B is a longwave image but A is xyz");
  });
});

describe("the A/B fixtures", () => {
  const a = fixture("ab-a.txt");
  const b = fixture("ab-b.txt");
  const inSquare = (x: number, y: number) => x >= 8 && x < 24 && y >= 8 && y < 24;

  it("flag most of the boosted square and about the noise rate elsewhere", () => {
    let inside = 0;
    let outside = 0;
    for (let y = 0; y < a.height; y++) {
      for (let x = 0; x < a.width; x++) {
        if (!diffAt(a, b, x, y, 1, 2).significant) continue;
        if (inSquare(x, y)) inside++;
        else outside++;
      }
    }
    const insideFraction = inside / 256;
    const outsideFraction = outside / (a.width * a.height - 256);
    expect(insideFraction).toBeGreaterThan(0.8);
    expect(outsideFraction).toBeGreaterThan(0.02);
    expect(outsideFraction).toBeLessThan(0.08);
  });

  it("summarize the significant pixels and pick a range from them", () => {
    const summary = summarizeDiff(a, b, 1, 2);
    expect(summary.finite).toBe(a.width * a.height);
    expect(summary.significant).toBeGreaterThan(200);
    expect(summary.autoRange.delta).toBeGreaterThan(0);
    expect(summary.autoRange.z).toBeGreaterThan(2);
  });
});

describe("noiseOnlyFraction", () => {
  it("matches the two-sided normal tail", () => {
    expect(noiseOnlyFraction(0)).toBeCloseTo(1, 6);
    expect(noiseOnlyFraction(1)).toBeCloseTo(0.3173, 4);
    expect(noiseOnlyFraction(2)).toBeCloseTo(0.0455, 4);
    expect(noiseOnlyFraction(3)).toBeCloseTo(0.0027, 4);
  });
});
