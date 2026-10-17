// Viewer settings in the URL hash, so a view can be bookmarked or shared.
//
//   #a=/runs/renders/r1/image.txt&m=map&q=1&s=relative&pal=viridis&r=pct:1:99&cmp=diff&k=3&v=32,24,8
//
// Dropped files are local and not part of the URL. Images the viewer can
// fetch from its own origin can be: `a` and `b` are paths (such as the
// render agent's outputs under /runs, served by the dev server) to open as
// A and B.
//
// Decoding is forgiving: unknown keys and invalid values are ignored, so a
// hand-edited or stale link degrades to defaults instead of failing.

import type { Statistic } from "../format/derived";
import type { DiffView } from "../format/diff";
import type { Pair } from "../format/htrdrImage";
import type { RangeMode } from "../render/range";
import type { CompareMode, DisplayMode } from "../render/settings";

export interface UrlSettings {
  /** Same-origin paths of htrdr-images to open as A and B. */
  a?: string;
  b?: string;
  mode?: DisplayMode;
  exposure?: number;
  /** "auto" is htpp's automatic white. */
  white?: number | "auto";
  pair?: Pair;
  stat?: Statistic;
  palette?: string;
  range?: { mode: RangeMode; low?: number; high?: number; manual?: [number, number] };
  log?: boolean;
  compare?: CompareMode;
  split?: number;
  k?: number;
  diffView?: DiffView;
  diffPalette?: string;
  diffRange?: number | "auto";
  /** Image point at the canvas centre, and CSS pixels per image pixel. */
  view?: { cx: number; cy: number; zoom: number };
}

const num = (v: number) => String(Number(v.toPrecision(9)));

/**
 * A path the viewer may fetch: absolute on its own origin, with no scheme,
 * no protocol-relative "//", no backslashes and no ".." segments. Links are
 * shared, so a link must not make the viewer fetch from somewhere else.
 */
export function isSafeSource(path: string): boolean {
  return (
    path.startsWith("/") &&
    !path.startsWith("//") &&
    !path.includes("\\") &&
    !path.includes(":") &&
    !path.split("/").includes("..") &&
    path.length <= 1024
  );
}

export function encodeSettings(s: UrlSettings): string {
  const p = new URLSearchParams();
  if (s.a) p.set("a", s.a);
  if (s.b) p.set("b", s.b);
  if (s.mode) p.set("m", s.mode);
  if (s.exposure !== undefined) p.set("exp", num(s.exposure));
  if (s.white !== undefined) p.set("w", s.white === "auto" ? "auto" : num(s.white));
  if (s.pair !== undefined) p.set("q", String(s.pair));
  if (s.stat) p.set("s", s.stat);
  if (s.palette) p.set("pal", s.palette);
  if (s.range) {
    const r = s.range;
    if (r.mode === "auto") p.set("r", "auto");
    else if (r.mode === "percentile") p.set("r", `pct:${num(r.low ?? 1)}:${num(r.high ?? 99)}`);
    else p.set("r", `man:${num(r.manual?.[0] ?? 0)}:${num(r.manual?.[1] ?? 1)}`);
  }
  if (s.log) p.set("log", "1");
  if (s.compare) p.set("cmp", s.compare);
  if (s.split !== undefined) p.set("split", num(s.split));
  if (s.k !== undefined) p.set("k", num(s.k));
  if (s.diffView) p.set("dv", s.diffView);
  if (s.diffPalette) p.set("dpal", s.diffPalette);
  if (s.diffRange !== undefined) p.set("dr", s.diffRange === "auto" ? "auto" : num(s.diffRange));
  if (s.view) p.set("v", [s.view.cx, s.view.cy, s.view.zoom].map(num).join(","));
  // URLSearchParams escapes ":" and ","; they are safe in a fragment and
  // much easier to read unescaped.
  return p.toString().replace(/%3A/gi, ":").replace(/%2C/gi, ",").replace(/%2F/gi, "/");
}

const finite = (text: string | null): number | undefined => {
  if (text === null || text.trim() === "") return undefined;
  const v = Number(text);
  return Number.isFinite(v) ? v : undefined;
};

function oneOf<T extends string>(text: string | null, options: readonly T[]): T | undefined {
  return options.find((o) => o === text);
}

export function decodeSettings(hash: string): UrlSettings {
  const p = new URLSearchParams(hash.replace(/^#/, ""));
  const s: UrlSettings = {};

  for (const key of ["a", "b"] as const) {
    const path = p.get(key);
    if (path && isSafeSource(path)) s[key] = path;
  }

  const mode = oneOf(p.get("m"), ["image", "map"] as const);
  if (mode) s.mode = mode;
  const exposure = finite(p.get("exp"));
  if (exposure !== undefined && exposure > 0) s.exposure = exposure;
  const w = p.get("w");
  if (w === "auto") s.white = "auto";
  else if ((finite(w) ?? 0) > 0) s.white = finite(w);
  const q = finite(p.get("q"));
  if (q !== undefined && Number.isInteger(q) && q >= 0 && q <= 3) s.pair = q as Pair;
  const stat = oneOf(p.get("s"), ["value", "sigma", "relative"] as const);
  if (stat) s.stat = stat;
  const pal = p.get("pal");
  if (pal && /^[a-z0-9]+$/.test(pal)) s.palette = pal;

  const r = p.get("r");
  if (r === "auto") s.range = { mode: "auto" };
  else if (r) {
    const [kind, a, b] = r.split(":");
    const lo = finite(a ?? null);
    const hi = finite(b ?? null);
    if (lo !== undefined && hi !== undefined) {
      if (kind === "pct" && [lo, hi].every((v) => v >= 0 && v <= 100)) s.range = { mode: "percentile", low: lo, high: hi };
      if (kind === "man") s.range = { mode: "manual", manual: [lo, hi] };
    }
  }
  if (p.get("log") === "1") s.log = true;
  else if (p.get("log") === "0") s.log = false;

  const cmp = oneOf(p.get("cmp"), ["none", "split", "diff"] as const);
  if (cmp) s.compare = cmp;
  const split = finite(p.get("split"));
  if (split !== undefined && split >= 0 && split <= 1) s.split = split;
  const k = finite(p.get("k"));
  if (k !== undefined && k >= 0) s.k = k;
  const dv = oneOf(p.get("dv"), ["delta", "z"] as const);
  if (dv) s.diffView = dv;
  const dpal = p.get("dpal");
  if (dpal && /^[a-z0-9]+$/.test(dpal)) s.diffPalette = dpal;
  const dr = p.get("dr");
  if (dr === "auto") s.diffRange = "auto";
  else if ((finite(dr) ?? 0) > 0) s.diffRange = finite(dr);

  const v = p.get("v")?.split(",").map((t) => finite(t));
  if (v && v.length === 3 && v.every((x) => x !== undefined) && v[2]! > 0) s.view = { cx: v[0]!, cy: v[1]!, zoom: v[2]! };
  return s;
}
