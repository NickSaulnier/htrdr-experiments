// Pan/zoom transform. All canvas coordinates are device pixels from the
// top-left; image coordinates are pixels from the image's top-left corner.

export interface View {
  /** Image point shown at the canvas centre. */
  cx: number;
  cy: number;
  /** Device pixels per image pixel. */
  scale: number;
}

export const MIN_SCALE = 1 / 64;
export const MAX_SCALE = 512;

const clampScale = (s: number) => Math.min(Math.max(s, MIN_SCALE), MAX_SCALE);

export function fitView(imageW: number, imageH: number, canvasW: number, canvasH: number, margin = 0.95): View {
  return { cx: imageW / 2, cy: imageH / 2, scale: clampScale(Math.min(canvasW / imageW, canvasH / imageH) * margin) };
}

export function canvasToImage(view: View, canvasW: number, canvasH: number, px: number, py: number): [number, number] {
  return [(px - canvasW / 2) / view.scale + view.cx, (py - canvasH / 2) / view.scale + view.cy];
}

/** Zoom by `factor`, keeping the image point under (px, py) fixed. */
export function zoomAt(view: View, canvasW: number, canvasH: number, px: number, py: number, factor: number): View {
  const scale = clampScale(view.scale * factor);
  const [ix, iy] = canvasToImage(view, canvasW, canvasH, px, py);
  return {
    scale,
    cx: ix - (px - canvasW / 2) / scale,
    cy: iy - (py - canvasH / 2) / scale,
  };
}

/** Move the image by (dx, dy) device pixels. */
export function panBy(view: View, dx: number, dy: number): View {
  return { ...view, cx: view.cx - dx / view.scale, cy: view.cy - dy / view.scale };
}

/** The integer pixel under a canvas point, or null outside the image. */
export function pixelAt(
  view: View,
  canvasW: number,
  canvasH: number,
  imageW: number,
  imageH: number,
  px: number,
  py: number,
): [number, number] | null {
  const [ix, iy] = canvasToImage(view, canvasW, canvasH, px, py);
  const x = Math.floor(ix);
  const y = Math.floor(iy);
  return x >= 0 && y >= 0 && x < imageW && y < imageH ? [x, y] : null;
}
