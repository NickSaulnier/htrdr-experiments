import { describe, expect, it } from "vitest";
import { decodeSettings, encodeSettings, isSafeSource, type UrlSettings } from "../src/ui/urlState";

describe("URL hash settings", () => {
  const full: UrlSettings = {
    a: "/runs/renders/r20261017-180748-004-19552b-19165/image.txt",
    b: "/runs/renders/r20261017-180742-003-19552b-2048/image.txt",
    mode: "map",
    exposure: 0.25,
    white: 173.41,
    pair: 1,
    stat: "relative",
    palette: "viridis",
    range: { mode: "percentile", low: 1, high: 99 },
    log: true,
    compare: "diff",
    split: 0.375,
    k: 3,
    diffView: "z",
    diffPalette: "rdbu",
    diffRange: 4.5,
    view: { cx: 32.5, cy: 24, zoom: 8 },
  };

  it("round-trips every setting", () => {
    expect(decodeSettings(`#${encodeSettings(full)}`)).toEqual(full);
  });

  it("stays readable", () => {
    expect(encodeSettings({ mode: "map", range: { mode: "manual", manual: [0.1, 5] }, view: { cx: 1, cy: 2, zoom: 3 } })).toBe(
      "m=map&r=man:0.1:5&v=1,2,3",
    );
  });

  it("encodes auto white and auto diff range", () => {
    const s = decodeSettings(encodeSettings({ white: "auto", diffRange: "auto", range: { mode: "auto" } }));
    expect(s).toEqual({ white: "auto", diffRange: "auto", range: { mode: "auto" } });
  });

  it("trims float noise but keeps meaningful digits", () => {
    expect(encodeSettings({ exposure: 0.1 + 0.2 })).toBe("exp=0.3");
    expect(decodeSettings(encodeSettings({ white: 123.456789 })).white).toBe(123.456789);
  });

  it("ignores unknown keys and invalid values", () => {
    const s = decodeSettings(
      "#m=video&exp=-1&w=abc&q=7&s=median&pal=<b>&r=pct:5:200&cmp=both&split=2&k=-1&dv=x&dr=0&v=1,2&zz=1",
    );
    expect(s).toEqual({});
  });

  it("accepts an empty or missing hash", () => {
    expect(decodeSettings("")).toEqual({});
    expect(decodeSettings("#")).toEqual({});
  });

  it("decodes escaped separators too", () => {
    expect(decodeSettings("#r=pct%3A2%3A98&v=1%2C2%2C3")).toEqual({
      range: { mode: "percentile", low: 2, high: 98 },
      view: { cx: 1, cy: 2, zoom: 3 },
    });
  });
});

describe("image sources", () => {
  it("keep paths readable in the hash", () => {
    expect(encodeSettings({ a: "/runs/renders/r1/image.txt", mode: "map" })).toBe("a=/runs/renders/r1/image.txt&m=map");
  });

  it.each([
    ["/runs/renders/r1/image.txt", true],
    ["/fixtures/xyz.txt", true],
    ["runs/x.txt", false], // relative
    ["//evil.example/x.txt", false], // protocol-relative: another origin
    ["https://evil.example/x.txt", false],
    ["/runs/../../etc/passwd", false],
    ["/runs\\x.txt", false], // a backslash
    ["/" + "x".repeat(2000), false],
  ])("%s safe: %s", (path, ok) => {
    expect(isSafeSource(path)).toBe(ok);
  });

  it("drops unsafe sources when decoding", () => {
    expect(decodeSettings("#a=https://evil.example/x.txt&b=//evil.example/y&m=map")).toEqual({ mode: "map" });
  });
});
