// Palettes and the CPU reference of htpp's map mode.
//
// htpp uses star-cmap's builtin palettes. We don't copy its tables. Instead
// they are rebuilt from permissively licensed upstream sources
// (paletteData.ts), and Moreland's cool-warm and jet are computed here.
// Missing from star-cmap's list: chromajs, gnpu, parula, sand, whgnbu,
// whylrd and ylrd, whose tables have no clear license (parula belongs to
// MathWorks).

import { PALETTE_DATA } from "./paletteData";

export type RGB = readonly [number, number, number];

export interface Palette {
  name: string;
  /** Control colors in [0, 1], at least one. */
  colors: readonly RGB[];
}

export const GRAY: Palette = { name: "gray", colors: [[0, 0, 0], [1, 1, 1]] };

function fromHex(hex: string): RGB {
  const n = parseInt(hex.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/** matplotlib's jet: piecewise linear, so 9 samples reproduce it exactly. */
function jet(): Palette {
  const ramp = (t: number, c: number) => Math.min(Math.max(1.5 - Math.abs(4 * t - c), 0), 1);
  const colors = Array.from({ length: 9 }, (_, i): RGB => [ramp(i / 8, 3), ramp(i / 8, 2), ramp(i / 8, 1)]);
  return { name: "jet", colors };
}

// --- Moreland's cool-warm (K. Moreland, "Diverging Color Maps for Scientific
// Visualization", 2009): interpolation in Msh space through white.

type Vec3 = [number, number, number];
const WHITE_D65: Vec3 = [0.95047, 1.0, 1.08883];

function srgbToMsh([r, g, b]: RGB): Vec3 {
  const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  const [R, G, B] = [lin(r), lin(g), lin(b)];
  const xyz: Vec3 = [
    0.4124564 * R + 0.3575761 * G + 0.1804375 * B,
    0.2126729 * R + 0.7151522 * G + 0.072175 * B,
    0.0193339 * R + 0.119192 * G + 0.9503041 * B,
  ];
  const f = (t: number) => (t > 216 / 24389 ? Math.cbrt(t) : (24389 / 27 * t + 16) / 116);
  const [fx, fy, fz] = xyz.map((v, i) => f(v / WHITE_D65[i]!)) as Vec3;
  const L = 116 * fy - 16;
  const a = 500 * (fx - fy);
  const bb = 200 * (fy - fz);
  const M = Math.hypot(L, a, bb);
  return [M, Math.acos(L / M), Math.atan2(bb, a)];
}

function mshToSrgb([M, s, h]: Vec3): RGB {
  const L = M * Math.cos(s);
  const a = M * Math.sin(s) * Math.cos(h);
  const b = M * Math.sin(s) * Math.sin(h);
  const fy = (L + 16) / 116;
  const finv = (t: number) => (t ** 3 > 216 / 24389 ? t ** 3 : (116 * t - 16) / (24389 / 27));
  const [X, Y, Z] = [finv(fy + a / 500), finv(fy), finv(fy - b / 200)].map((v, i) => v * WHITE_D65[i]!) as Vec3;
  const R = 3.2404542 * X - 1.5371385 * Y - 0.4985314 * Z;
  const G = -0.969266 * X + 1.8760108 * Y + 0.041556 * Z;
  const B = 0.0556434 * X - 0.2040259 * Y + 1.0572252 * Z;
  const enc = (c: number) => Math.min(Math.max(c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055, 0), 1);
  return [enc(R), enc(G), enc(B)];
}

function adjustHue([M, s, h]: Vec3, unsaturatedM: number): number {
  if (M >= unsaturatedM) return h;
  const spin = (s * Math.sqrt(unsaturatedM ** 2 - M ** 2)) / (M * Math.sin(s));
  return h > -Math.PI / 3 ? h + spin : h - spin;
}

function divergingMsh(c1: RGB, c2: RGB, t: number): RGB {
  let m1 = srgbToMsh(c1);
  let m2 = srgbToMsh(c2);
  if (m1[1] > 0.05 && m2[1] > 0.05 && Math.abs(m1[2] - m2[2]) > Math.PI / 3) {
    const mid = Math.max(m1[0], m2[0], 88);
    if (t < 0.5) {
      m2 = [mid, 0, 0];
      t *= 2;
    } else {
      m1 = [mid, 0, 0];
      t = 2 * t - 1;
    }
  }
  if (m1[1] < 0.05 && m2[1] > 0.05) m1[2] = adjustHue(m2, m1[0]);
  else if (m2[1] < 0.05 && m1[1] > 0.05) m2[2] = adjustHue(m1, m2[0]);
  return mshToSrgb([0, 1, 2].map((i) => (1 - t) * m1[i]! + t * m2[i]!) as Vec3);
}

/** `samples` colors of Moreland's diverging map between two end colors. */
export function morelandColors(cool: RGB, warm: RGB, samples: number): RGB[] {
  return Array.from({ length: samples }, (_, i) => divergingMsh(cool, warm, i / (samples - 1)));
}

function moreland(): Palette {
  return { name: "moreland", colors: morelandColors([0.230, 0.299, 0.754], [0.706, 0.016, 0.150], 257) };
}

export const PALETTES: readonly Palette[] = [
  GRAY,
  jet(),
  moreland(),
  ...Object.entries(PALETTE_DATA).map(([name, colors]) => ({ name, colors: colors.map(fromHex) })),
].sort((a, b) => a.name.localeCompare(b.name));

/** htpp's default palette. */
export const DEFAULT_PALETTE = "inferno";

export function paletteByName(name: string): Palette {
  return PALETTES.find((p) => p.name === name) ?? GRAY;
}

/** star-cmap's scmap_fetch_color with SCMAP_FILTER_LINEAR. */
export function fetchColor(palette: Palette, value: number): RGB {
  const { colors } = palette;
  const scaled = value * (colors.length - 1);
  const index = Math.min(Math.floor(scaled), colors.length - 1);
  const t = scaled - index;
  const a = colors[index]!;
  const b = colors[Math.min(index + 1, colors.length - 1)]!;
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** htpp's pp_map for one value. A zero-width range maps to black. */
export function mapValue(value: number, range: readonly [number, number], palette: Palette): RGB {
  const size = range[1] - range[0];
  if (size === 0) return [0, 0, 0];
  const v = Math.min(Math.max((value - range[0]) / size, 0), 1);
  return fetchColor(palette, v);
}

/**
 * Entries in the GPU lookup table. Large enough that linear filtering between
 * entries stays within one 8-bit step of the exact palette, even at the kinks
 * of an 8-color palette. Must match LUT_SIZE in view.frag.
 */
export const LUT_SIZE = 1024;

/** Bake a palette into an RGBA8 lookup table for the GPU. */
export function bakeLut(palette: Palette, size = LUT_SIZE): Uint8Array {
  const lut = new Uint8Array(size * 4);
  for (let i = 0; i < size; i++) {
    const [r, g, b] = fetchColor(palette, i / (size - 1));
    lut.set([Math.round(r * 255), Math.round(g * 255), Math.round(b * 255), 255], i * 4);
  }
  return lut;
}
