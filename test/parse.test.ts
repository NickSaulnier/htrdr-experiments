import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { detectLayout, quantities } from "../src/format/htrdrImage";
import { HtrdrParseError, parseHtrdrImage } from "../src/format/parse";

const fixture = (name: string) => readFileSync(join(import.meta.dirname, "..", "fixtures", name));

describe("parseHtrdrImage", () => {
  it("parses the definition and pixels row-major", () => {
    const img = parseHtrdrImage("2 1\n1 2 3 4 5 6 7 8\n9 10 11 12 13 14 15 16\n");
    expect(img.width).toBe(2);
    expect(img.height).toBe(1);
    expect(Array.from(img.data)).toEqual(Array.from({ length: 16 }, (_, i) => i + 1));
  });

  it("skips comments, blank lines and CRLF line endings", () => {
    const text = "# header\r\n\r\n1 1 # definition\r\n  # only a comment\r\n1e-3 2.5E+2 -3 0.5 +5 .25 7 8 # (1,1)\r\n";
    const img = parseHtrdrImage(text);
    expect(Array.from(img.data)).toEqual([1e-3, 250, -3, 0.5, 5, 0.25, 7, 8].map(Math.fround));
  });

  it("accepts 6-value pixels and zero-fills the time pair, like htpp", () => {
    const img = parseHtrdrImage("1 1\n1 2 3 4 5 6\n");
    expect(Array.from(img.data)).toEqual([1, 2, 3, 4, 5, 6, 0, 0]);
  });

  it("reads printf spellings of infinity and NaN", () => {
    const img = parseHtrdrImage("1 1\nnan -nan inf -inf 1 1 1 1\n");
    expect(Number.isNaN(img.data[0])).toBe(true);
    expect(Number.isNaN(img.data[1])).toBe(true);
    expect(img.data[2]).toBe(Infinity);
    expect(img.data[3]).toBe(-Infinity);
  });

  it("parses decimals exactly, including ones outside the fast path", () => {
    const tokens = ["2.55e-4", "0.1", "123456789012345678", "1e-30", "4.2e+30", "-0.0", "3.0000000000000000001", "1e300"];
    const img = parseHtrdrImage(`1 1\n${tokens.join(" ")}\n`);
    expect(Array.from(img.data)).toEqual(tokens.map((t) => Math.fround(Number(t))));
  });

  it("reports the line of a malformed value", () => {
    expect(() => parseHtrdrImage("1 2\n1 2 3 4 5 6 7 8\n1 2 3 x 5 6 7 8\n")).toThrow(/line 3: invalid number "x"/);
    expect(() => parseHtrdrImage("1 1\n1 2 3 4 5 6 7 1e\n")).toThrow(/invalid number "1e"/);
  });

  it("rejects pixel lines with too few or too many values", () => {
    expect(() => parseHtrdrImage("1 1\n1 2 3 4 5\n")).toThrow(/line 2: expected 6 to 8 values per pixel, found 5/);
    expect(() => parseHtrdrImage("1 1\n1 2 3 4 5 6 7 8 9\n")).toThrow(/found 9/);
  });

  it("rejects bad definitions", () => {
    expect(() => parseHtrdrImage("")).toThrow(/missing image definition/);
    expect(() => parseHtrdrImage("# nothing\n")).toThrow(/missing image definition/);
    expect(() => parseHtrdrImage("3\n")).toThrow(/line 1: expected the image definition/);
    expect(() => parseHtrdrImage("3 -2\n")).toThrow(/invalid image height "-2"/);
    expect(() => parseHtrdrImage("0 2\n")).toThrow(/invalid image definition 0x2/);
  });

  it("rejects truncated and overlong files", () => {
    expect(() => parseHtrdrImage("2 1\n1 2 3 4 5 6 7 8\n")).toThrow(/truncated image: expected 2 pixels, found 1/);
    expect(() => parseHtrdrImage("1 1\n1 2 3 4 5 6 7 8\n1 2 3 4 5 6 7 8\n")).toThrow(/line 3: more pixels/);
  });

  it("throws HtrdrParseError carrying the line number", () => {
    try {
      parseHtrdrImage("1 1\n\n\nbad\n");
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(HtrdrParseError);
      expect((err as HtrdrParseError).line).toBe(4);
    }
  });

  it("reports progress ending at 1", () => {
    const seen: number[] = [];
    parseHtrdrImage(fixture("xyz.txt"), { onProgress: (f) => seen.push(f) });
    expect(seen.length).toBeGreaterThan(1);
    expect(seen.at(-1)).toBe(1);
    expect(seen).toEqual([...seen].sort((a, b) => a - b));
  });
});

describe("layout detection", () => {
  it.each([
    ["xyz.txt", "xyz"],
    ["longwave.txt", "longwave"],
    ["shortwave.txt", "shortwave"],
    ["ab-a.txt", "xyz"],
  ] as const)("%s is %s", (name, layout) => {
    const img = parseHtrdrImage(fixture(name));
    expect(img.width).toBe(64);
    expect(img.height).toBe(48);
    expect(detectLayout(img)).toBe(layout);
  });

  it("lists only the meaningful quantities per layout", () => {
    expect(quantities("xyz").map((q) => q.label)).toEqual(["X", "Y", "Z", "Time per path"]);
    expect(quantities("longwave").map((q) => q.pair)).toEqual([0, 1, 3]);
    expect(quantities("shortwave").map((q) => q.pair)).toEqual([1, 3]);
  });
});
