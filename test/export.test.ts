import { describe, expect, it } from "vitest";
import { GRAY, type Palette } from "../src/color/palettes";
import { formatG, parsePPM, ppmChunks, toGnuplot, toPPM } from "../src/export/formats";

describe("formatG matches C's %g", () => {
  it.each([
    [0, "0"],
    [1, "1"],
    [0.5, "0.5"],
    [100, "100"],
    [123456, "123456"],
    [1234567, "1.23457e+06"],
    [0.0001, "0.0001"],
    [0.00001, "1e-05"],
    [0.000123456789, "0.000123457"],
    [-2.5e-7, "-2.5e-07"],
    [1.1, "1.1"],
    [0.05, "0.05"],
    [0.75, "0.75"],
    [999999.5, "1e+06"],
    [3.14159265, "3.14159"],
    [1e100, "1e+100"],
    [NaN, "nan"],
    [Infinity, "inf"],
    [-Infinity, "-inf"],
  ])("%s → %s", (v, s) => {
    expect(formatG(v)).toBe(s);
  });
});

describe("PPM", () => {
  const rgba = new Uint8Array([255, 0, 10, 255, 1, 2, 3, 0, 4, 5, 6, 255, 7, 8, 9, 255]);

  it("writes htpp's P3 layout: header, then one pixel per line", () => {
    expect(toPPM(rgba, 2, 2)).toBe("P3 2 2\n255\n255 0 10\n1 2 3\n4 5 6\n7 8 9\n");
    expect(ppmChunks(rgba, 2, 2)).toHaveLength(3);
  });

  it("round-trips through the parser, ignoring alpha", () => {
    const { width, height, rgba: back } = parsePPM(toPPM(rgba, 2, 2));
    expect([width, height]).toEqual([2, 2]);
    expect(Array.from(back)).toEqual([255, 0, 10, 255, 1, 2, 3, 255, 4, 5, 6, 255, 7, 8, 9, 255]);
  });

  it("rejects what it can't read", () => {
    expect(() => parsePPM("P6 1 1 255 xyz")).toThrow(/not a P3 PPM/);
    expect(() => parsePPM("P3 1 1 65535 1 2 3")).toThrow(/maximum 65535/);
    expect(() => parsePPM("P3 2 1 255 1 2 3")).toThrow(/3 values, expected 6/);
  });
});

describe("gnuplot", () => {
  const palette: Palette = { name: "p", colors: [[0, 0, 0], [0.5, 0.25, 1]] };
  const script = toGnuplot({
    width: 3,
    height: 2,
    values: new Float32Array([1, 2, 3, 4, 5, 6]),
    palette,
    range: [1, 6],
    log: false,
    title: "Y of x.txt",
  });
  const lines = script.split("\n");

  it("follows htpp's img_write_gnuplot layout", () => {
    expect(lines.slice(0, 13)).toEqual([
      "# Y of x.txt",
      "unset xtics",
      "unset ytics",
      "unset key",
      "unset colorbox",
      "unset origin",
      "unset border",
      "unset title",
      "unset margin",
      "set margins 0,0,0,0",
      "set xrange[0:2]",
      "set yrange[1:0]",
      "set cbrange[1:6]",
    ]);
    expect(lines.slice(13, 17)).toEqual([
      "set terminal png size 3,2*(1+0.08+0.02)",
      "set origin 0, 0.05",
      "set size ratio 0.666667",
      "set colorbox horiz user origin 0.1,0.04 size 0.8,0.04",
    ]);
  });

  it("lists palette entries and pixels as row, column, value, rows separated by a blank line", () => {
    const body = script.slice(script.indexOf("set palette"));
    expect(body).toBe(
      "set palette defined (\\\n  0 0 0 0,\\\n  1 0.5 0.25 1)\n" +
        "$map2 << EOD\n0 0 1\n0 1 2\n0 2 3\n\n1 0 4\n1 1 5\n1 2 6\nEOD\n" +
        "plot '$map2' using 2:1:3 with image pixels\n",
    );
  });

  it("adds log scaling and drops an empty range", () => {
    const log = toGnuplot({ width: 1, height: 1, values: new Float32Array([1]), palette: GRAY, range: [2, 2], log: true, title: "t" });
    expect(log).not.toContain("cbrange");
    expect(log).toContain("set logscale cb\n");
  });
});
