// Mouse pan/zoom for the canvas. Keyboard zoom lives in shortcuts.ts.

import { panBy, zoomAt, type View } from "../render/view";

export interface ViewportHost {
  getView(): View | null;
  setView(view: View): void;
  fit(): void;
  /** Cursor moved to a device-pixel position on the canvas, or left it. */
  hover(px: number, py: number): void;
  leave(): void;
}

const WHEEL_ZOOM_RATE = 0.0015;

export function attachViewportControls(canvas: HTMLCanvasElement, host: ViewportHost): void {
  const toDevice = (e: { clientX: number; clientY: number }): [number, number] => {
    const rect = canvas.getBoundingClientRect();
    const dpr = canvas.width / rect.width;
    return [(e.clientX - rect.left) * dpr, (e.clientY - rect.top) * dpr];
  };

  canvas.addEventListener(
    "wheel",
    (e) => {
      const view = host.getView();
      if (!view) return;
      e.preventDefault();
      // Lines and pages arrive with much smaller deltas than pixels.
      const delta = e.deltaY * (e.deltaMode === 1 ? 33 : e.deltaMode === 2 ? 400 : 1);
      const [px, py] = toDevice(e);
      host.setView(zoomAt(view, canvas.width, canvas.height, px, py, Math.exp(-delta * WHEEL_ZOOM_RATE)));
    },
    { passive: false },
  );

  let drag: { id: number; x: number; y: number } | null = null;
  canvas.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !host.getView()) return;
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch {
      // Not an active pointer (synthetic events); panning still works over the canvas.
    }
    const [x, y] = toDevice(e);
    drag = { id: e.pointerId, x, y };
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", (e) => {
    const [x, y] = toDevice(e);
    const view = host.getView();
    if (drag && drag.id === e.pointerId && view) {
      host.setView(panBy(view, x - drag.x, y - drag.y));
      drag.x = x;
      drag.y = y;
    }
    host.hover(x, y);
  });
  const endDrag = (e: PointerEvent) => {
    if (drag?.id !== e.pointerId) return;
    drag = null;
    canvas.classList.remove("dragging");
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("pointerleave", () => host.leave());
  canvas.addEventListener("dblclick", () => host.fit());
}
