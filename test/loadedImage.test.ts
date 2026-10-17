import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { loadFromBytes, pixelValue } from "../src/format/loadedImage";

const encode = (text: string) => new TextEncoder().encode(text);

describe("loadFromBytes", () => {
  it("splits the 8 values into two RGBA planes", () => {
    const img = loadFromBytes(encode("2 1\n1 2 3 4 5 6 7 8\n9 10 11 12 13 14 15 16\n"));
    expect(Array.from(img.planes[0])).toEqual([1, 2, 3, 4, 9, 10, 11, 12]);
    expect(Array.from(img.planes[1])).toEqual([5, 6, 7, 8, 13, 14, 15, 16]);
  });

  it("looks up any value of any pixel from the top-left", () => {
    const rows = Array.from({ length: 6 }, (_, i) => Array.from({ length: 8 }, (_, k) => i * 10 + k).join(" "));
    const img = loadFromBytes(encode(`3 2\n${rows.join("\n")}\n`));
    expect(pixelValue(img, 0, 0, 0)).toBe(0);
    expect(pixelValue(img, 2, 0, 5)).toBe(25);
    expect(pixelValue(img, 0, 1, 3)).toBe(33);
    expect(pixelValue(img, 2, 1, 7)).toBe(57);
  });

  it("detects the layout and computes stats", () => {
    const bytes = readFileSync(join(import.meta.dirname, "..", "fixtures", "longwave.txt"));
    const img = loadFromBytes(bytes);
    expect(img.layout).toBe("longwave");
    const t = img.stats.values[0]!;
    expect(t.min).toBeGreaterThan(200);
    expect(t.max).toBeLessThan(320);
  });
});
