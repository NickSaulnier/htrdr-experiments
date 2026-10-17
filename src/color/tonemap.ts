// CPU reference of htpp's image mode, ported line for line from htpp.c
// (filmic_tone_mapping, XYZ_to_sRGB, sRGB_gamma_correct, img_write_ppm).
// The fragment shader must match this to within one 8-bit step.

const A = 0.15;
const B = 0.5;
const C = 0.1;
const D = 0.2;
const E = 0.02;
const F = 0.3;

/** Uncharted 2 filmic curve, htpp's TONE_MAP macro. T(0) = 0. */
export function filmic(x: number): number {
  return (x * (A * x + C * B) + D * E) / (x * (A * x + B) + D * F) - E / F;
}

/**
 * The same curve over a common denominator. Algebraically identical, but it
 * avoids subtracting E/F from a nearly equal quotient, so it keeps its
 * precision near 0 in float32. The shader uses this form.
 */
export function filmicStable(x: number): number {
  return (x * ((F - E) * A * x + (F * C - E) * B)) / (F * (x * (A * x + B) + D * F));
}

const D65_x = 0.31271;
const D65_y = 0.32902;
/** htpp multiplies XYZ componentwise by this before the matrix. */
export const D65 = [D65_x / D65_y, 1.0, (1 - D65_x - D65_y) / D65_y] as const;

/** XYZ to linear sRGB, row-major (htpp stores it column-major). */
export const XYZ_TO_SRGB = [
  [3.2404542, -1.5371385, -0.4985314],
  [-0.969266, 1.8760108, 0.041556],
  [0.0556434, -0.2040259, 1.0572252],
] as const;

export function srgbEncode(value: number): number {
  return value <= 0.0031308 ? value * 12.92 : 1.055 * Math.pow(value, 1.0 / 2.4) - 0.055;
}

/** Quantize like htpp's img_write_ppm. */
export function toByte(value: number): number {
  const clamped = Math.min(Math.max(value, 0), 1);
  return Math.trunc(clamped * 255.0 + 0.5);
}

export interface ToneMapParams {
  exposure: number;
  /** Luminance mapped to white (htpp's `white`, Ymax). */
  white: number;
}

/** Map one XYZ pixel to encoded sRGB in [0, 1], before quantization. */
export function toneMapPixel(X: number, Y: number, Z: number, { exposure, white }: ToneMapParams): [number, number, number] {
  const whiteScale = filmic(white * exposure);
  const x = (filmic(X * exposure) / whiteScale) * D65[0];
  const y = (filmic(Y * exposure) / whiteScale) * D65[1];
  const z = (filmic(Z * exposure) / whiteScale) * D65[2];
  const rgb = XYZ_TO_SRGB.map((row) => srgbEncode(Math.max(row[0] * x + row[1] * y + row[2] * z, 0)));
  return rgb as [number, number, number];
}

/** Same as toneMapPixel, quantized to the 8-bit values htpp writes. */
export function toneMapPixelBytes(X: number, Y: number, Z: number, params: ToneMapParams): [number, number, number] {
  return toneMapPixel(X, Y, Z, params).map(toByte) as [number, number, number];
}
