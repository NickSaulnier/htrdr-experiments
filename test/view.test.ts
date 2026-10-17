import { describe, expect, it } from "vitest";
import { MAX_SCALE, MIN_SCALE, canvasToImage, fitView, panBy, pixelAt, zoomAt } from "../src/render/view";

describe("view transform", () => {
  it("fits the limiting dimension with a margin and centres the image", () => {
    expect(fitView(200, 100, 1000, 1000, 1)).toEqual({ cx: 100, cy: 50, scale: 5 });
    expect(fitView(200, 100, 1000, 200, 1)).toEqual({ cx: 100, cy: 50, scale: 2 });
    expect(fitView(200, 100, 1000, 1000).scale).toBeCloseTo(4.75);
  });

  it("maps the canvas centre to the view centre", () => {
    expect(canvasToImage({ cx: 10, cy: 20, scale: 4 }, 800, 600, 400, 300)).toEqual([10, 20]);
    expect(canvasToImage({ cx: 10, cy: 20, scale: 4 }, 800, 600, 0, 0)).toEqual([-90, -55]);
  });

  it("keeps the point under the cursor fixed while zooming", () => {
    const view = { cx: 32, cy: 24, scale: 3 };
    const before = canvasToImage(view, 640, 480, 100, 70);
    const zoomed = zoomAt(view, 640, 480, 100, 70, 2.5);
    expect(zoomed.scale).toBeCloseTo(7.5);
    const after = canvasToImage(zoomed, 640, 480, 100, 70);
    expect(after[0]).toBeCloseTo(before[0], 10);
    expect(after[1]).toBeCloseTo(before[1], 10);
  });

  it("clamps zoom to the scale limits", () => {
    expect(zoomAt({ cx: 0, cy: 0, scale: 1 }, 10, 10, 5, 5, 1e9).scale).toBe(MAX_SCALE);
    expect(zoomAt({ cx: 0, cy: 0, scale: 1 }, 10, 10, 5, 5, 1e-9).scale).toBe(MIN_SCALE);
  });

  it("pans by device pixels", () => {
    expect(panBy({ cx: 10, cy: 10, scale: 2 }, 20, -4)).toEqual({ cx: 0, cy: 12, scale: 2 });
  });

  it("finds the integer pixel under a point and nothing outside", () => {
    // 4x2 image at scale 10 on a 40x20 canvas: pixel (x, y) spans [10x, 10x+10).
    const view = fitView(4, 2, 40, 20, 1);
    expect(pixelAt(view, 40, 20, 4, 2, 0, 0)).toEqual([0, 0]);
    expect(pixelAt(view, 40, 20, 4, 2, 39.9, 19.9)).toEqual([3, 1]);
    expect(pixelAt(view, 40, 20, 4, 2, 15, 12)).toEqual([1, 1]);
    expect(pixelAt(view, 40, 20, 4, 2, -0.1, 5)).toBeNull();
    expect(pixelAt(view, 40, 20, 4, 2, 40, 5)).toBeNull();
  });
});
