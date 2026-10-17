// In-memory model of an htrdr-image(5) file.
//
// Each pixel is 8 reals: four (mean, standard error) pairs. What a pair
// means depends on the htrdr spectral mode that produced the file, which the
// file itself does not record (see PLAN.md §1).

/** Number of values stored per pixel. */
export const PIXEL_STRIDE = 8;

export type Layout = "xyz" | "longwave" | "shortwave";

export interface HtrdrImage {
  width: number;
  height: number;
  /** Row-major from the top-left, PIXEL_STRIDE values per pixel. */
  data: Float32Array;
}

/** Index of a (mean, σ) pair inside a pixel. */
export type Pair = 0 | 1 | 2 | 3;

export const TIME_PAIR: Pair = 3;

export const meanIndex = (pair: Pair): number => pair * 2;
export const sigmaIndex = (pair: Pair): number => pair * 2 + 1;

export interface Quantity {
  pair: Pair;
  label: string;
  unit: string;
}

const TIME: Quantity = { pair: TIME_PAIR, label: "Time per path", unit: "µs" };
const RADIANCE_UNIT = "W/sr/m²";

/** The meaningful pairs of a layout, in menu order. Time is always last. */
export function quantities(layout: Layout): Quantity[] {
  switch (layout) {
    case "xyz":
      return [
        { pair: 0, label: "X", unit: RADIANCE_UNIT },
        { pair: 1, label: "Y", unit: RADIANCE_UNIT },
        { pair: 2, label: "Z", unit: RADIANCE_UNIT },
        TIME,
      ];
    case "longwave":
      return [
        { pair: 0, label: "Brightness temperature", unit: "K" },
        { pair: 1, label: "Radiance", unit: RADIANCE_UNIT },
        TIME,
      ];
    case "shortwave":
      return [{ pair: 1, label: "Radiance", unit: RADIANCE_UNIT }, TIME];
  }
}

function pairIsZero(img: HtrdrImage, pair: Pair): boolean {
  const { data } = img;
  const m = meanIndex(pair);
  for (let i = m; i < data.length; i += PIXEL_STRIDE) {
    if (data[i] !== 0 || data[i + 1] !== 0) return false;
  }
  return true;
}

/**
 * Guess the spectral layout. Longwave and shortwave images leave pair 2
 * unused, and shortwave also leaves pair 0 unused. A CIE XYZ image of a
 * black scene is indistinguishable from shortwave; the UI lets the user
 * override the guess.
 */
export function detectLayout(img: HtrdrImage): Layout {
  if (!pairIsZero(img, 2)) return "xyz";
  return pairIsZero(img, 0) ? "shortwave" : "longwave";
}
