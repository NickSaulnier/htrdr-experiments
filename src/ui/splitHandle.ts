// Draggable split divider over the canvas. Its position is a fraction of the
// stage width, so it stays put relative to the window while the image pans.

export function attachSplitHandle(handle: HTMLElement, stage: HTMLElement, onMove: (fraction: number) => void): void {
  let dragging: number | null = null;
  const fractionAt = (clientX: number) => {
    const rect = stage.getBoundingClientRect();
    return Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
  };
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    dragging = e.pointerId;
    try {
      handle.setPointerCapture(e.pointerId);
    } catch {
      // Not an active pointer (synthetic events); dragging still works while
      // the pointer stays over the handle.
    }
  });
  handle.addEventListener("pointermove", (e) => {
    if (dragging === e.pointerId) onMove(fractionAt(e.clientX));
  });
  const end = (e: PointerEvent) => {
    if (dragging === e.pointerId) dragging = null;
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
}

export function placeSplitHandle(handle: HTMLElement, fraction: number): void {
  handle.style.left = `${fraction * 100}%`;
}
