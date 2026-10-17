// File formats for exports, mirroring htpp's writers where htpp has one.
// Pure functions (no DOM), so Node scripts and tests can use them.

import type { Palette } from "../color/palettes";

/** C's printf("%g"): 6 significant digits, trailing zeros stripped. */
export function formatG(value: number, precision = 6): string {
  if (Number.isNaN(value)) return value < 0 ? "-nan" : "nan";
  if (!Number.isFinite(value)) return value < 0 ? "-inf" : "inf";
  if (value === 0) return Object.is(value, -0) ? "-0" : "0";
  // toPrecision rounds like %g and picks fixed notation for exponents in
  // [-6, precision); C switches to exponential below -4, so redo those.
  let s = value.toPrecision(precision);
  if (!s.includes("e") && Math.abs(Number(s)) < 1e-4) s = value.toExponential(precision - 1);
  const e = s.indexOf("e");
  if (e < 0) return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
  const mantissa = s.slice(0, e);
  const exp = Number(s.slice(e + 1));
  const m = mantissa.includes(".") ? mantissa.replace(/\.?0+$/, "") : mantissa;
  return `${m}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
}

/**
 * Plain-text PPM exactly as htpp's img_write_ppm writes it, one chunk per
 * image row so a 4K export never builds one giant string.
 */
export function ppmChunks(rgba: Uint8Array, width: number, height: number): string[] {
  const chunks = [`P3 ${width} ${height}\n255\n`];
  for (let y = 0; y < height; y++) {
    let row = "";
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4;
      row += `${rgba[i]} ${rgba[i + 1]} ${rgba[i + 2]}\n`;
    }
    chunks.push(row);
  }
  return chunks;
}

export const toPPM = (rgba: Uint8Array, width: number, height: number): string => ppmChunks(rgba, width, height).join("");

/** Parse a P3 PPM (as htpp writes it) into RGBA. */
export function parsePPM(text: string): { width: number; height: number; rgba: Uint8Array } {
  const tokens = text.replace(/#[^\n]*/g, "").split(/\s+/).filter(Boolean);
  if (tokens[0] !== "P3") throw new Error(`not a P3 PPM (starts with "${tokens[0]}")`);
  const [width, height, max] = tokens.slice(1, 4).map(Number) as [number, number, number];
  if (max !== 255) throw new Error(`unsupported PPM maximum ${max}`);
  const values = tokens.slice(4).map(Number);
  if (values.length !== width * height * 3) throw new Error(`PPM has ${values.length} values, expected ${width * height * 3}`);
  const rgba = new Uint8Array(width * height * 4);
  for (let i = 0; i < width * height; i++) {
    rgba.set([values[i * 3]!, values[i * 3 + 1]!, values[i * 3 + 2]!, 255], i * 4);
  }
  return { width, height, rgba };
}

export interface GnuplotOptions {
  width: number;
  height: number;
  /** One value per pixel, row-major from the top-left. */
  values: Float32Array;
  palette: Palette;
  range: readonly [number, number];
  log: boolean;
  /** Written as a leading comment. */
  title: string;
}

/**
 * A gnuplot script in the layout of htpp's img_write_gnuplot: the image
 * fills the plot, row 0 at the top, with a horizontal colorbox below it.
 * Unlike htpp it always sets the range on screen, and adds log scaling when
 * the view uses it.
 */
export function toGnuplot(options: GnuplotOptions): string {
  return gnuplotChunks(options).join("");
}

export function gnuplotChunks({ width, height, values, palette, range, log, title }: GnuplotOptions): string[] {
  const cboxWidth = 0.8;
  const cboxHeight = 0.08;
  const cboxTopMargin = 0.02;
  const out: string[] = [
    `# ${title.replace(/\n/g, " ")}\n`,
    "unset xtics\n",
    "unset ytics\n",
    "unset key\n",
    "unset colorbox\n",
    "unset origin\n",
    "unset border\n",
    "unset title\n",
    "unset margin\n",
    "set margins 0,0,0,0\n",
    `set xrange[0:${width - 1}]\n`,
    `set yrange[${height - 1}:0]\n`,
  ];
  if (range[0] < range[1]) out.push(`set cbrange[${formatG(range[0])}:${formatG(range[1])}]\n`);
  if (log) out.push("set logscale cb\n");
  out.push(
    `set terminal png size ${width},${height}*(1+${formatG(cboxHeight)}+${formatG(cboxTopMargin)})\n`,
    `set origin 0, ${formatG((cboxHeight + cboxTopMargin) * 0.5)}\n`,
    `set size ratio ${formatG(height / width)}\n`,
    `set colorbox horiz user origin ${formatG((1 - cboxWidth) * 0.5)},${formatG(cboxHeight * 0.5)} size ${formatG(cboxWidth)},${formatG(cboxHeight * 0.5)}\n`,
    "set palette defined (\\\n",
    palette.colors.map((c, i) => `  ${i} ${formatG(c[0])} ${formatG(c[1])} ${formatG(c[2])}`).join(",\\\n"),
    ")\n",
    "$map2 << EOD\n",
  );
  for (let y = 0; y < height; y++) {
    const row = new Array<string>(width);
    for (let x = 0; x < width; x++) row[x] = `${y} ${x} ${formatG(values[y * width + x]!)}\n`;
    out.push(row.join(""));
    if (y !== height - 1) out.push("\n");
  }
  out.push("EOD\n", "plot '$map2' using 2:1:3 with image pixels\n");
  return out;
}
