// Parser for the htrdr-image(5) text format.
//
//   <definition> := <width> <height>
//   <pixel>      := 8 reals, one pixel per line, row-major from the top-left
//
// `#` starts a comment and blank lines are skipped. Like htpp, a pixel line
// may carry only 6 values, in which case the time pair defaults to 0.

import { PIXEL_STRIDE, type HtrdrImage } from "./htrdrImage";

export class HtrdrParseError extends Error {
  constructor(
    message: string,
    /** 1-based line number in the source, or 0 when not tied to a line. */
    readonly line: number,
  ) {
    super(line > 0 ? `line ${line}: ${message}` : message);
    this.name = "HtrdrParseError";
  }
}

export interface ParseOptions {
  /** Called with the fraction of bytes consumed, roughly every 64 KiB. */
  onProgress?: (fraction: number) => void;
}

const MIN_VALUES = 6;
const PROGRESS_STEP = 1 << 16;

const CH_NL = 0x0a;
const CH_CR = 0x0d;
const CH_SP = 0x20;
const CH_TAB = 0x09;
const CH_HASH = 0x23;
const CH_0 = 0x30;
const CH_9 = 0x39;
const CH_DOT = 0x2e;
const CH_PLUS = 0x2b;
const CH_MINUS = 0x2d;
const CH_E = 0x45;
const CH_e = 0x65;

// Powers of ten that are exact in binary64; dividing or multiplying an exact
// integer mantissa below 2^53 by one of these is correctly rounded.
const POW10 = Array.from({ length: 23 }, (_, i) => Number(`1e${i}`));
const MAX_FAST_DIGITS = 15;

const decoder = new TextDecoder();

/**
 * Parse one numeric token. The fast path handles plain decimals exactly
 * (Clinger's fast path); everything else goes through Number(), with C's
 * printf spellings of infinity and NaN accepted.
 */
function parseReal(bytes: Uint8Array, start: number, end: number): number {
  let i = start;
  let negative = false;
  const first = bytes[i];
  if (first === CH_MINUS || first === CH_PLUS) {
    negative = first === CH_MINUS;
    i++;
  }

  let mantissa = 0;
  let digits = 0;
  let exponent = 0;
  let sawDigit = false;
  while (i < end) {
    const c = bytes[i]!;
    if (c < CH_0 || c > CH_9) break;
    sawDigit = true;
    if (mantissa !== 0 || c !== CH_0) digits++;
    mantissa = mantissa * 10 + (c - CH_0);
    i++;
  }
  if (i < end && bytes[i] === CH_DOT) {
    i++;
    while (i < end) {
      const c = bytes[i]!;
      if (c < CH_0 || c > CH_9) break;
      sawDigit = true;
      if (mantissa !== 0 || c !== CH_0) digits++;
      mantissa = mantissa * 10 + (c - CH_0);
      exponent--;
      i++;
    }
  }
  if (sawDigit && i < end && (bytes[i] === CH_E || bytes[i] === CH_e)) {
    i++;
    let expNegative = false;
    if (bytes[i] === CH_MINUS || bytes[i] === CH_PLUS) {
      expNegative = bytes[i] === CH_MINUS;
      i++;
    }
    let exp = 0;
    let sawExpDigit = false;
    while (i < end) {
      const c = bytes[i]!;
      if (c < CH_0 || c > CH_9) break;
      sawExpDigit = true;
      exp = Math.min(exp * 10 + (c - CH_0), 100000);
      i++;
    }
    if (!sawExpDigit) return slowParseReal(bytes, start, end);
    exponent += expNegative ? -exp : exp;
  }

  if (i !== end || !sawDigit || digits > MAX_FAST_DIGITS || exponent < -22 || exponent > 22) {
    return slowParseReal(bytes, start, end);
  }
  const value = exponent < 0 ? mantissa / POW10[-exponent]! : mantissa * POW10[exponent]!;
  return negative ? -value : value;
}

function slowParseReal(bytes: Uint8Array, start: number, end: number): number {
  const token = decoder.decode(bytes.subarray(start, end));
  const lower = token.toLowerCase().replace(/^\+/, "");
  if (lower === "inf" || lower === "infinity") return Infinity;
  if (lower === "-inf" || lower === "-infinity") return -Infinity;
  // NaN here means either a literal NaN or garbage; isNumeric tells them apart.
  return Number(token);
}

function isNumeric(value: number, bytes: Uint8Array, start: number, end: number): boolean {
  if (!Number.isNaN(value)) return true;
  const lower = decoder.decode(bytes.subarray(start, end)).toLowerCase();
  return lower === "nan" || lower === "-nan" || lower === "+nan";
}

/** Parse an htrdr-image file. Throws HtrdrParseError on malformed input. */
export function parseHtrdrImage(input: Uint8Array | string, options: ParseOptions = {}): HtrdrImage {
  const bytes = typeof input === "string" ? new TextEncoder().encode(input) : input;
  const { onProgress } = options;
  const n = bytes.length;

  let width = 0;
  let height = 0;
  let data: Float32Array | null = null;
  let pixelCount = 0;
  let expected = 0;
  let nextProgress = PROGRESS_STEP;

  // Token bounds for the current line, reused across lines.
  const starts: number[] = [];
  const ends: number[] = [];

  let line = 0;
  let pos = 0;
  while (pos < n) {
    line++;
    // Tokenize one line, stopping at a comment.
    starts.length = 0;
    ends.length = 0;
    let inComment = false;
    while (pos < n) {
      const c = bytes[pos]!;
      if (c === CH_NL) {
        pos++;
        break;
      }
      if (inComment || c === CH_SP || c === CH_TAB || c === CH_CR) {
        pos++;
        continue;
      }
      if (c === CH_HASH) {
        inComment = true;
        pos++;
        continue;
      }
      const start = pos;
      while (pos < n) {
        const d = bytes[pos]!;
        if (d === CH_SP || d === CH_TAB || d === CH_CR || d === CH_NL || d === CH_HASH) break;
        pos++;
      }
      starts.push(start);
      ends.push(pos);
    }

    if (onProgress && pos >= nextProgress) {
      onProgress(pos / n);
      nextProgress = pos + PROGRESS_STEP;
    }

    const count = starts.length;
    if (count === 0) continue;

    if (data === null) {
      if (count !== 2) {
        throw new HtrdrParseError(`expected the image definition "<width> <height>", found ${count} values`, line);
      }
      const dims = [0, 1].map((k) => {
        const text = decoder.decode(bytes.subarray(starts[k]!, ends[k]!));
        if (!/^\d+$/.test(text)) {
          throw new HtrdrParseError(`invalid image ${k === 0 ? "width" : "height"} "${text}"`, line);
        }
        return Number(text);
      });
      width = dims[0]!;
      height = dims[1]!;
      if (width === 0 || height === 0) {
        throw new HtrdrParseError(`invalid image definition ${width}x${height}`, line);
      }
      expected = width * height;
      data = new Float32Array(expected * PIXEL_STRIDE);
      continue;
    }

    if (count < MIN_VALUES || count > PIXEL_STRIDE) {
      throw new HtrdrParseError(`expected ${MIN_VALUES} to ${PIXEL_STRIDE} values per pixel, found ${count}`, line);
    }
    if (pixelCount === expected) {
      throw new HtrdrParseError(`more pixels than the ${width}x${height} definition allows`, line);
    }
    const base = pixelCount * PIXEL_STRIDE;
    for (let k = 0; k < count; k++) {
      const s = starts[k]!;
      const e = ends[k]!;
      const value = parseReal(bytes, s, e);
      if (!isNumeric(value, bytes, s, e)) {
        throw new HtrdrParseError(`invalid number "${decoder.decode(bytes.subarray(s, e))}"`, line);
      }
      data[base + k] = value;
    }
    pixelCount++;
  }

  if (data === null) throw new HtrdrParseError("missing image definition", 0);
  if (pixelCount !== expected) {
    throw new HtrdrParseError(`truncated image: expected ${expected} pixels, found ${pixelCount}`, 0);
  }
  onProgress?.(1);
  return { width, height, data };
}
